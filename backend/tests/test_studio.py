from __future__ import annotations

import base64
import time

import pytest
from fastapi.testclient import TestClient

from vaak.product.bootstrap import build_runtime
from vaak.product.config import ProductConfig
from vaak.product.api import create_app
from vaak.provenance import UtteranceReceipt
from vaak.studio import (
    DirectorIntent,
    RightsScope,
    StudioCharacter,
    StudioError,
    StudioSessionRequest,
    UsageClass,
    VoiceOrigin,
)


def rt():
    return build_runtime(ProductConfig(environment="development", allow_dev_providers=True))


def provision_character(runtime, *, character_id="character_nova", performer=False):
    ch = StudioCharacter(
        character_id=character_id,
        studio_id="studio_orbit",
        display_name="Nova",
        persona={"cadence": "precise", "warmth": 0.55},
        voice_origin=VoiceOrigin.LICENSED_PERFORMER if performer else VoiceOrigin.SYNTHETIC,
        performer_id="performer_7" if performer else None,
        canonical_voice_version="season-2",
    )
    runtime.studio.create_character(ch, rights_holder_id="rights_orbit")
    runtime.set_studio_envelope(ch.character_id)
    enr = runtime.open_voice_enrollment(ch.character_id)
    runtime.complete_voice_enrollment(
        ch.character_id,
        session_id=enr["session_id"],
        pcm16=b"\x01\x00" * 8000,
        spoken_challenge=enr["challenge"],
        languages=("en", "es"),
    )
    grant = runtime.studio.issue_rights(
        rights_holder_id="rights_orbit",
        character_id=ch.character_id,
        scope=RightsScope(
            production_ids=("film_42",),
            languages=("en", "es"),
            territories=("worldwide",),
            channels=("local", "stream"),
            usage_classes=(UsageClass.DIALOGUE.value, UsageClass.DUBBING.value, UsageClass.BROADCAST.value),
        ),
        ttl_seconds=3600,
    )
    return ch, grant


def test_studio_character_session_binds_rights_production_and_conditioning():
    runtime = rt(); ch, grant = provision_character(runtime)
    live = runtime.studio.open_session(ch.character_id, StudioSessionRequest(
        grant_id=grant.grant_id,
        production_id="film_42",
        usage_class="dialogue",
        language="en",
        director=DirectorIntent(scene_id="12A", take_id="3", emotion_direction="restrained concern", pacing="measured"),
    ))
    pcm = b"".join(__import__("asyncio").run(_collect(runtime, live.session_id, "We should go now.")))
    assert pcm
    manifest = runtime.close_live(live.session_id)
    assert manifest["identity_class"] == "studio_character"
    assert manifest["principal_id"] == ch.character_id
    assert manifest["rights_grant_hash"] == grant.grant_hash()
    assert manifest["production_id"] == "film_42"
    assert manifest["performance_version"] == "season-2"
    assert manifest["conditioning_hash"]


def test_studio_receipt_verifies_rights_holder_signature_offline():
    runtime = rt(); ch, grant = provision_character(runtime)
    live = runtime.studio.open_session(ch.character_id, StudioSessionRequest(grant_id=grant.grant_id, production_id="film_42"))
    __import__("asyncio").run(_collect(runtime, live.session_id, "Proof this performance."))
    manifest = runtime.close_live(live.session_id)
    runtime.core.anchor()
    rec = runtime.core.log.receipt(manifest["manifest_hash"])
    trust = runtime.core.verification_trust_bundle(ch.character_id)
    assert UtteranceReceipt.verify(rec, trust)
    assert rec.authorization_evidence["identity_context"]["rights_grant"]["grant_id"] == grant.grant_id


def test_studio_scope_blocks_wrong_production_language_and_usage():
    runtime = rt(); ch, grant = provision_character(runtime)
    with pytest.raises(StudioError, match="production not granted"):
        runtime.studio.open_session(ch.character_id, StudioSessionRequest(grant_id=grant.grant_id, production_id="film_99"))
    with pytest.raises(StudioError, match="language not granted"):
        runtime.studio.open_session(ch.character_id, StudioSessionRequest(grant_id=grant.grant_id, production_id="film_42", language="fr"))
    with pytest.raises(StudioError, match="usage class not granted"):
        runtime.studio.open_session(ch.character_id, StudioSessionRequest(grant_id=grant.grant_id, production_id="film_42", usage_class="promo"))


def test_licensed_performer_grant_has_dual_consent_signature():
    runtime = rt(); ch, grant = provision_character(runtime, performer=True)
    assert grant.performer_id == "performer_7"
    assert grant.performer_signature and grant.performer_public_key_pem
    grant.verify_signatures()



def test_licensed_performer_receipt_verifier_checks_performer_signature():
    runtime = rt(); ch, grant = provision_character(runtime, performer=True)
    live = runtime.studio.open_session(ch.character_id, StudioSessionRequest(grant_id=grant.grant_id, production_id="film_42"))
    __import__("asyncio").run(_collect(runtime, live.session_id, "licensed performance proof"))
    manifest = runtime.close_live(live.session_id)
    runtime.core.anchor()
    rec = runtime.core.log.receipt(manifest["manifest_hash"])
    trust = runtime.core.verification_trust_bundle(ch.character_id)
    assert UtteranceReceipt.verify(rec, trust)
    rights = rec.authorization_evidence["identity_context"]["rights_grant"]
    assert rights["performer_signature"]


def test_rights_revocation_terminates_active_character_session_and_blocks_reopen():
    runtime = rt(); ch, grant = provision_character(runtime)
    live = runtime.studio.open_session(ch.character_id, StudioSessionRequest(grant_id=grant.grant_id, production_id="film_42"))
    runtime.studio.revoke_rights(grant.grant_id, "license ended")
    assert live.session_id not in runtime.core.sessions
    assert live.session_id not in runtime.live
    with pytest.raises(StudioError, match="revoked"):
        runtime.studio.open_session(ch.character_id, StudioSessionRequest(grant_id=grant.grant_id, production_id="film_42"))


def test_rights_expiry_is_rechecked_before_audio_emission():
    runtime = rt(); ch, _ = provision_character(runtime)
    grant = runtime.studio.rights.issue(
        rights_holder_id="rights_orbit", studio_id=ch.studio_id, character=ch,
        scope=RightsScope(production_ids=("flash",), channels=("local",), usage_classes=("dialogue",)),
        ttl_seconds=0.01,
    )
    live = runtime.studio.open_session(ch.character_id, StudioSessionRequest(grant_id=grant.grant_id, production_id="flash"))
    time.sleep(0.02)
    with pytest.raises(Exception, match="studio rights grant expired"):
        __import__("asyncio").run(_collect(runtime, live.session_id, "too late"))


def test_studio_api_and_voice_model_spec():
    runtime = rt(); app = create_app(runtime, api_keys=("secret",)); c = TestClient(app)
    h = {"Authorization": "Bearer secret"}
    spec = c.get("/v1/voice-model/spec", headers=h)
    assert spec.status_code == 200 and spec.json()["supports_studio_direction"] is True
    r = c.post("/v1/studios/s1/characters", headers=h, json={
        "character_id":"char_api", "rights_holder_id":"rh1", "display_name":"Mira", "voice_origin":"synthetic",
        "persona":{"style":"cinematic"}, "canonical_voice_version":"v3"
    })
    assert r.status_code == 200
    e = c.post("/v1/principals/char_api/voice/enrollment", headers=h).json()
    done = c.post("/v1/principals/char_api/voice/enrollment/complete", headers=h, json={
        "session_id":e["session_id"], "audio_b64":base64.b64encode(b"\x01\x00"*8000).decode(),
        "spoken_challenge":e["challenge"], "languages":["en"]
    })
    assert done.status_code == 200
    rights = c.post("/v1/studios/s1/characters/char_api/rights", headers=h, json={
        "rights_holder_id":"rh1", "production_ids":["game7"], "channels":["local"], "usage_classes":["interactive"]
    })
    assert rights.status_code == 200
    grant_id = rights.json()["grant_id"]
    live = c.post("/v1/studios/s1/characters/char_api/live", headers=h, json={
        "grant_id":grant_id, "production_id":"game7", "usage_class":"interactive", "register":"stage",
        "director":{"scene_id":"intro", "energy":0.4}
    })
    assert live.status_code == 200 and live.json()["mode"] == "studio"


async def _collect(runtime, session_id: str, text: str):
    return [c async for c in runtime.speak_stream(session_id, text)]


def test_vaak_voice_model_plan_is_identity_and_rights_bound():
    from vaak.modeling import VaakModelPlan
    from vaak.voice_model import VoiceConditioning, VoiceIdentityClass
    a = VoiceConditioning(principal_id="nova", identity_class=VoiceIdentityClass.STUDIO_CHARACTER, persona={"tone":"warm"}, rights_grant_hash="a"*64, production={"production_id":"film_42"}, performance_version="s2")
    b = VoiceConditioning(principal_id="nova", identity_class=VoiceIdentityClass.STUDIO_CHARACTER, persona={"tone":"warm"}, rights_grant_hash="b"*64, production={"production_id":"film_42"}, performance_version="s2")
    pa = VaakModelPlan.from_conditioning(a.body())
    pb = VaakModelPlan.from_conditioning(b.body())
    assert pa.plan_hash() != pb.plan_hash()
    assert "<vaak:class=studio_character>" in pa.control_tokens()
    assert any(t.startswith("<vaak:rights=") for t in pa.control_tokens())
