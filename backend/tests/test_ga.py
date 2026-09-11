from __future__ import annotations

import asyncio
import base64
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vaak.agency import AgencyAssessment, InfluenceIntent
from vaak.media.contracts import AudioChunk, Transcript, VoiceProfile
from vaak.media.ensemble import QualityGateError, QualityRouter, QualitySnapshot, QualityStore, RoutedASR, RoutedTTS
from vaak.product.api import create_app
from vaak.product.bootstrap import build_runtime
from vaak.product.classifier import EnsembleAgencyClassifier
from vaak.product.config import ProductConfig
from vaak.product.model_catalog import DEFAULT_MODEL_FAMILIES
from vaak.product.principals import PrincipalProfile
from vaak.product.security import TenantAuthStore
from vaak.voice_model import VoiceIdentityClass


def runtime(tmp_path: Path):
    return build_runtime(ProductConfig(
        environment="development",
        allow_dev_providers=True,
        state_db_path=str(tmp_path / "state.db"),
        auth_db_path=str(tmp_path / "auth.db"),
        quality_db_path=str(tmp_path / "quality.db"),
        dev_evidence_secret="x" * 40,
    ))


def provision(rt, tenant="t1", principal="p1"):
    rt.register_principal(PrincipalProfile(principal, "owner", "Voice", tenant_id=tenant, identity_class=VoiceIdentityClass.DEVELOPER_AGENT))
    e = rt.open_voice_enrollment(principal, tenant_id=tenant)
    rt.complete_voice_enrollment(principal, session_id=e["session_id"], pcm16=b"\x01\x00" * 8000, spoken_challenge=e["challenge"], tenant_id=tenant)
    rt.set_default_envelope(principal, tenant_id=tenant)
    return e


def test_tenant_object_isolation(tmp_path):
    rt = runtime(tmp_path)
    auth = TenantAuthStore(str(tmp_path / "auth.db"))
    a = auth.issue_token("tenant-a", "alice", ("*",))
    b = auth.issue_token("tenant-b", "bob", ("*",))
    c = TestClient(create_app(rt, auth=auth))
    ra = c.post("/v1/principals", headers={"Authorization": f"Bearer {a}"}, json={"principal_id":"pa","controller_id":"alice","display_name":"A"})
    assert ra.status_code == 200
    rb = c.post("/v1/principals/pa/voice/enrollment", headers={"Authorization": f"Bearer {b}"})
    assert rb.status_code == 403


def test_caller_cannot_self_assert_presence(tmp_path):
    rt = runtime(tmp_path); provision(rt)
    auth = TenantAuthStore(str(tmp_path / "auth.db")); tok = auth.issue_token("t1", "u", ("*",)); auth.bind_resource("t1", "principal", "p1")
    c = TestClient(create_app(rt, auth=auth)); h={"Authorization":f"Bearer {tok}"}
    r = c.post("/v1/principals/p1/live", headers=h, json={"channel":"call","register":"professional","presence_checked":True})
    assert r.status_code == 400
    assert "presence" in r.text.lower()


def test_verified_presence_and_real_disclosure_unlock_call(tmp_path):
    rt = runtime(tmp_path); provision(rt)
    presence = rt.presence_verifier.mint(tenant_id="t1", principal_id="p1")
    live = rt.open_live("p1", tenant_id="t1", channel=__import__("vaak.envelope", fromlist=["ChannelClass"]).ChannelClass.CALL, register=__import__("vaak.envelope", fromlist=["Register"]).Register.PROFESSIONAL, presence_evidence=presence)
    with pytest.raises(Exception, match="disclosure required"):
        asyncio.run(_collect(rt, live.session_id, "hello"))
    pcm, meta = rt.render_disclosure(live.session_id)
    assert pcm and meta["audio_sha256"]
    delivery = rt.disclosure_verifier.mint(**meta, transport_ref="corridor:test")
    confirmed = rt.confirm_disclosure(live.session_id, delivery)
    assert confirmed["disclosed"] is True
    assert asyncio.run(_collect(rt, live.session_id, "hello after disclosure"))
    manifest = rt.close_live(live.session_id)
    assert manifest["presence_evidence_hash"]
    assert manifest["disclosure_evidence_hash"]
    assert manifest["disclosure_audio_sha256"] == meta["audio_sha256"]


def test_enrollment_binds_verifier_evidence(tmp_path):
    rt = runtime(tmp_path)
    rt.register_principal(PrincipalProfile("p", "o", "P", tenant_id="t"))
    e = rt.open_voice_enrollment("p", tenant_id="t")
    r = rt.complete_voice_enrollment("p", session_id=e["session_id"], pcm16=b"\x01\x00"*100, spoken_challenge=e["challenge"], tenant_id="t")
    assert r["enrollment_evidence"]["challenge_detected"] is True
    consent = rt.core.enrollment.consent_for(r["asset_id"])
    assert consent.enrollment_evidence_hash


def test_principal_state_survives_runtime_restart(tmp_path):
    r1 = runtime(tmp_path)
    r1.register_principal(PrincipalProfile("persist", "o", "Persist", tenant_id="tenant-x"))
    r2 = runtime(tmp_path)
    assert r2.get_principal("persist", tenant_id="tenant-x").display_name == "Persist"
    with pytest.raises(PermissionError):
        r2.get_principal("persist", tenant_id="other")


class FakeASR:
    def __init__(self, text): self.text=text
    async def transcribe(self, pcm16, *, sample_rate=16000, language=None): return Transcript(self.text, language or "en")


class FakeTTS:
    sample_rate=16000
    def __init__(self, value=b"\x01\x00", fail_after=False): self.value=value; self.fail_after=fail_after
    async def synthesize(self, text, *, voice:VoiceProfile, language="English", prosody=None, conditioning=None):
        yield AudioChunk(self.value*160,16000)
        if self.fail_after: raise RuntimeError("boom")


def test_quality_router_prefers_quality_and_rejects_below_floor(tmp_path):
    store=QualityStore(str(tmp_path/"q.db")); router=QualityRouter(store)
    store.put(QualitySnapshot("qwen3_tts","tts",0.95,180,identity_similarity=0.94,cost_score=0.4))
    store.put(QualitySnapshot("nvidia_nemotron_tts","tts",0.88,90,identity_similarity=0.90,cost_score=0.6))
    assert router.ranked("tts", ["qwen3_tts","nvidia_nemotron_tts"])[0] == "qwen3_tts"
    store.put(QualitySnapshot("bad","tts",0.60,20,identity_similarity=0.95))
    assert "bad" not in router.ranked("tts", ["qwen3_tts","bad"])


def test_routed_tts_will_not_switch_mid_utterance(tmp_path):
    store=QualityStore(str(tmp_path/"q.db")); router=QualityRouter(store)
    store.put(QualitySnapshot("primary","tts",0.95,100,identity_similarity=0.95))
    store.put(QualitySnapshot("backup","tts",0.90,100,identity_similarity=0.90))
    routed=RoutedTTS({"primary":FakeTTS(fail_after=True),"backup":FakeTTS()},router)
    async def go():
        out=[]
        with pytest.raises(QualityGateError, match="mid-utterance"):
            async for c in routed.synthesize("x", voice=VoiceProfile("v")): out.append(c)
        assert out
    asyncio.run(go())


def test_agency_ensemble_merges_conservatively():
    class C:
        def __init__(self,a): self.a=a
        def classify(self,text): return self.a
    a=AgencyAssessment(intent=InfluenceIntent.INFORM,influence_intensity=0.1,high_stakes=False,risk_tags=("a",),classifier="llama")
    b=AgencyAssessment(intent=InfluenceIntent.PERSUADE_DISCLOSED,influence_intensity=0.9,high_stakes=True,permission_escalation=True,risk_tags=("b",),classifier="deepseek")
    r=EnsembleAgencyClassifier([C(a),C(b)]).classify("x")
    assert r.high_stakes and r.permission_escalation and r.influence_intensity == 0.9
    assert set(r.risk_tags)=={"a","b"}


def test_model_catalog_names_required_families():
    families={x["family"] for x in DEFAULT_MODEL_FAMILIES}
    assert {"Qwen3-TTS","NVIDIA Nemotron Speech TTS","NVIDIA Nemotron 3.5 ASR","Whisper","Mistral Voxtral","Meta Llama","DeepSeek"} <= families
    assert "Mistral Voxtral TTS" not in families


def test_production_config_rejects_incomplete_model_and_trust_stack(tmp_path):
    cfg=ProductConfig(environment="production", allow_dev_providers=False, state_db_path=str(tmp_path/"s"), auth_db_path=str(tmp_path/"a"), quality_db_path=str(tmp_path/"q"))
    with pytest.raises(RuntimeError, match="production config missing"):
        cfg.validate()


async def _collect(rt, sid, text):
    return [c async for c in rt.speak_stream(sid,text)]


def test_durable_voice_vault_survives_restart_and_shred(tmp_path):
    cfg = ProductConfig(
        environment="development", allow_dev_providers=True,
        state_db_path=str(tmp_path / "state.db"), auth_db_path=str(tmp_path / "auth.db"),
        quality_db_path=str(tmp_path / "quality.db"), voice_vault_db_path=str(tmp_path / "vault.db"),
        dev_evidence_secret="v" * 40,
    )
    r1 = build_runtime(cfg)
    r1.register_principal(PrincipalProfile("voice-persist", "owner", "Voice", tenant_id="tenant-v"))
    e = r1.open_voice_enrollment("voice-persist", tenant_id="tenant-v")
    out = r1.complete_voice_enrollment(
        "voice-persist", session_id=e["session_id"], pcm16=b"\x05\x00" * 1000,
        spoken_challenge=e["challenge"], tenant_id="tenant-v",
    )
    asset_id = out["asset_id"]
    assert r1.core.vault.load(asset_id)
    r2 = build_runtime(cfg)
    assert r2.core.assets["voice-persist"] == asset_id
    assert r2.core.vault.load(asset_id)
    r2.set_default_envelope("voice-persist", tenant_id="tenant-v")
    r2.erase_voice("voice-persist", tenant_id="tenant-v")
    r3 = build_runtime(cfg)
    assert r3.core.vault.is_shredded(asset_id)
    with pytest.raises(Exception, match="crypto-shredded"):
        r3.core.vault.load(asset_id)


def test_studio_character_and_rights_survive_restart(tmp_path):
    from vaak.studio import RightsScope, StudioCharacter, VoiceOrigin
    cfg = ProductConfig(
        environment="development", allow_dev_providers=True,
        state_db_path=str(tmp_path / "state.db"), auth_db_path=str(tmp_path / "auth.db"),
        quality_db_path=str(tmp_path / "quality.db"), voice_vault_db_path=str(tmp_path / "vault.db"),
        dev_evidence_secret="s" * 40,
    )
    r1 = build_runtime(cfg)
    character = StudioCharacter("char-persist", "studio-persist", "Persistent", voice_origin=VoiceOrigin.SYNTHETIC)
    r1.studio.create_character(character, rights_holder_id="holder-persist", tenant_id="tenant-s")
    grant = r1.studio.issue_rights(
        rights_holder_id="holder-persist", character_id=character.character_id,
        scope=RightsScope(production_ids=("prod-1",)), ttl_seconds=3600,
    )
    r2 = build_runtime(cfg)
    assert r2.studio.characters[character.character_id].studio_id == "studio-persist"
    restored = r2.studio.rights.get(grant.grant_id)
    restored.authorize(production_id="prod-1", language="en", territory="worldwide", channel="local", usage_class="dialogue")


def test_soft_binding_failure_blocks_egress(tmp_path):
    rt = runtime(tmp_path); provision(rt)
    live = rt.open_live("p1", tenant_id="t1")
    class FailBinder:
        def bind(self, *args, **kwargs):
            raise RuntimeError("binding service unavailable")
    rt.core.sessions[live.session_id]._soft_binder = FailBinder()
    with pytest.raises(RuntimeError, match="binding service unavailable"):
        asyncio.run(_collect(rt, live.session_id, "must not leave unbound"))


def test_soft_binding_reference_is_bound_into_manifest(tmp_path):
    from vaak.media.binding import BoundAudio
    rt = runtime(tmp_path); provision(rt)
    live = rt.open_live("p1", tenant_id="t1")
    class Binder:
        def bind(self, pcm16, *, sample_rate, context):
            return BoundAudio(pcm16, f"bind:{context['session_id']}:{context['frame_offset']}")
    rt.core.sessions[live.session_id]._soft_binder = Binder()
    assert asyncio.run(_collect(rt, live.session_id, "bound speech"))
    manifest = rt.close_live(live.session_id)
    assert manifest["soft_binding_count"] > 0
    assert manifest["soft_binding_refs_digest"]


def test_licensed_performer_requires_verified_identity_evidence(tmp_path):
    from vaak.product.evidence import HMACIdentityVerifier
    from vaak.studio import RightsScope, StudioCharacter, StudioRuntime, VoiceOrigin, StudioError
    rt = runtime(tmp_path)
    verifier = HMACIdentityVerifier(b"i" * 40)
    rt.studio = StudioRuntime(rt, identity_verifier=verifier, require_identity_evidence=True)
    character = StudioCharacter(
        "licensed-1", "studio-1", "Licensed", voice_origin=VoiceOrigin.LICENSED_PERFORMER,
        performer_id="performer-1",
    )
    with pytest.raises(StudioError, match="performer identity evidence required"):
        rt.studio.create_character(character, rights_holder_id="holder-1", tenant_id="tenant-i")
    performer_ev = verifier.mint(
        tenant_id="tenant-i", subject_id="performer-1", role="performer",
        purpose="studio_character:licensed-1",
    )
    created = rt.studio.create_character(
        character, rights_holder_id="holder-1", tenant_id="tenant-i",
        performer_identity_evidence=performer_ev,
    )
    assert created.performer_evidence_hash
    with pytest.raises(StudioError, match="rights-holder identity evidence required"):
        rt.studio.issue_rights(
            rights_holder_id="holder-1", character_id="licensed-1",
            scope=RightsScope(production_ids=("prod-i",)), ttl_seconds=3600,
        )
    holder_ev = verifier.mint(
        tenant_id="tenant-i", subject_id="holder-1", role="rights_holder",
        purpose="studio_rights:licensed-1",
    )
    grant = rt.studio.issue_rights(
        rights_holder_id="holder-1", character_id="licensed-1",
        scope=RightsScope(production_ids=("prod-i",)), ttl_seconds=3600,
        rights_holder_identity_evidence=holder_ev,
    )
    assert grant.performer_evidence_hash and grant.rights_holder_evidence_hash


def test_quality_router_rejects_stale_benchmark(tmp_path):
    import time
    store = QualityStore(str(tmp_path / "stale.db")); router = QualityRouter(store)
    store.put(QualitySnapshot(
        "stale-tts", "tts", 0.99, 100, identity_similarity=0.99,
        evaluated_at=time.time() - 31 * 24 * 3600,
    ))
    with pytest.raises(QualityGateError, match="no tts provider clears"):
        router.ranked("tts", ["stale-tts"])


def _ow(provider_id, family, role, endpoint):
    return {
        "provider_id": provider_id, "family": family, "role": role, "endpoint": endpoint,
        "model_id": f"pinned/{provider_id}", "weights_uri": f"https://weights.invalid/{provider_id}",
        "open_weights": True, "commercial_use_allowed": True, "license_id": "approved-test-license",
    }


def test_production_requires_two_agency_families(tmp_path):
    import json
    services = dict(
        environment="production", allow_dev_providers=False,
        state_db_path=str(tmp_path / "state.db"), auth_db_path=str(tmp_path / "auth.db"),
        quality_db_path=str(tmp_path / "quality.db"), voice_vault_db_path=str(tmp_path / "vault.db"),
        media_gateway_url="https://media.invalid", soft_binding_url="https://binding.invalid",
        enrollment_verifier_url="https://identity.invalid", evidence_verifier_url="https://evidence.invalid",
        hsm_url="https://hsm.invalid", chrysalis_url="https://chrysalis.invalid",
    )
    catalog = [
        _ow("qwen3_tts", "Qwen3-TTS", "tts", "https://tts.invalid"),
        _ow("nvidia_nemotron_asr", "NVIDIA Nemotron 3.5 ASR", "asr", "https://asr.invalid"),
        _ow("whisper_asr", "Whisper", "asr", "https://whisper.invalid"),
        _ow("mistral_voxtral_asr", "Mistral Voxtral", "asr", "https://voxtral.invalid"),
        _ow("meta_llama_agency", "Meta Llama", "agency", "https://agency.invalid"),
    ]
    snaps = [
        {"provider_id":"qwen3_tts","role":"tts","quality_score":0.95,"latency_p95_ms":200,"identity_similarity":0.95},
        {"provider_id":"nvidia_nemotron_asr","role":"asr","quality_score":0.95,"latency_p95_ms":200,"wer":0.05},
        {"provider_id":"meta_llama_agency","role":"agency","quality_score":0.95,"latency_p95_ms":200,"agency_false_negative_rate":0.01},
    ]
    cfg = ProductConfig(**services, model_catalog_json=json.dumps(catalog), quality_snapshots_json=json.dumps(snaps))
    with pytest.raises(RuntimeError, match="at least two independently configurable model families"):
        cfg.validate()


def test_studio_api_forwards_performer_identity_evidence(tmp_path):
    from vaak.product.evidence import HMACIdentityVerifier
    from vaak.studio import StudioRuntime
    rt = runtime(tmp_path)
    verifier = HMACIdentityVerifier(b"p" * 40)
    rt.studio = StudioRuntime(rt, identity_verifier=verifier, require_identity_evidence=True)
    auth = TenantAuthStore(str(tmp_path / "auth-api.db"))
    tok = auth.issue_token("tenant-api", "holder-api", ("*",))
    c = TestClient(create_app(rt, auth=auth)); h = {"Authorization": f"Bearer {tok}"}
    performer_ev = verifier.mint(
        tenant_id="tenant-api", subject_id="performer-api", role="performer",
        purpose="studio_character:char-api",
    )
    r = c.post(
        "/v1/studios/studio-api/characters", headers=h,
        json={
            "character_id":"char-api", "rights_holder_id":"holder-api", "display_name":"API Licensed",
            "voice_origin":"licensed_performer", "performer_id":"performer-api",
            "performer_identity_evidence":performer_ev,
        },
    )
    assert r.status_code == 200, r.text
    assert rt.studio.characters["char-api"].performer_evidence_hash


def test_production_requires_all_requested_model_families(tmp_path):
    import json
    services = dict(
        environment="production", allow_dev_providers=False,
        state_db_path=str(tmp_path / "state.db"), auth_db_path=str(tmp_path / "auth.db"),
        quality_db_path=str(tmp_path / "quality.db"), voice_vault_db_path=str(tmp_path / "vault.db"),
        media_gateway_url="https://media.invalid", soft_binding_url="https://binding.invalid",
        enrollment_verifier_url="https://identity.invalid", evidence_verifier_url="https://evidence.invalid",
        hsm_url="https://hsm.invalid", chrysalis_url="https://chrysalis.invalid",
    )
    catalog = [
        _ow("qwen3_tts", "Qwen3-TTS", "tts", "https://tts.invalid"),
        _ow("nvidia_nemotron_asr", "NVIDIA Nemotron 3.5 ASR", "asr", "https://asr.invalid"),
        _ow("meta_llama_agency", "Meta Llama", "agency", "https://llama.invalid"),
        _ow("deepseek_agency", "DeepSeek", "agency", "https://deepseek.invalid"),
    ]
    snaps = [
        {"provider_id":p["provider_id"], "role":p["role"], "quality_score":0.95,"latency_p95_ms":200,
         **({"identity_similarity":0.95} if p["role"]=="tts" else {}),
         **({"wer":0.05} if p["role"]=="asr" else {}),
         **({"agency_false_negative_rate":0.01} if p["role"]=="agency" else {})}
        for p in catalog
    ]
    cfg = ProductConfig(**services, model_catalog_json=json.dumps(catalog), quality_snapshots_json=json.dumps(snaps))
    with pytest.raises(RuntimeError, match="missing required open-weight families"):
        cfg.validate()


def _full_open_weight_catalog():
    return [
        _ow("qwen3_tts", "Qwen3-TTS", "tts", "https://tts.invalid"),
        _ow("nvidia_nemotron_asr", "NVIDIA Nemotron 3.5 ASR", "asr", "https://nemotron.invalid"),
        _ow("whisper_asr", "Whisper", "asr", "https://whisper.invalid"),
        _ow("mistral_voxtral_asr", "Mistral Voxtral", "asr", "https://voxtral.invalid"),
        _ow("meta_llama_agency", "Meta Llama", "agency", "https://llama.invalid"),
        _ow("deepseek_agency", "DeepSeek", "agency", "https://deepseek.invalid"),
    ]


def _snapshots(catalog):
    return [
        {"provider_id":p["provider_id"], "role":p["role"], "quality_score":0.95, "latency_p95_ms":200,
         **({"identity_similarity":0.95} if p["role"]=="tts" else {}),
         **({"wer":0.05} if p["role"]=="asr" else {}),
         **({"agency_false_negative_rate":0.01} if p["role"]=="agency" else {})}
        for p in catalog
    ]


def _prod_services(tmp_path):
    return dict(
        environment="production", allow_dev_providers=False,
        state_db_path=str(tmp_path / "state.db"), auth_db_path=str(tmp_path / "auth.db"),
        quality_db_path=str(tmp_path / "quality.db"), voice_vault_db_path=str(tmp_path / "vault.db"),
        media_gateway_url="https://media.invalid", soft_binding_url="https://binding.invalid",
        enrollment_verifier_url="https://identity.invalid", evidence_verifier_url="https://evidence.invalid",
        hsm_url="https://hsm.invalid", chrysalis_url="https://chrysalis.invalid",
    )


def test_production_rejects_closed_weight_provider(tmp_path):
    import json
    catalog = _full_open_weight_catalog()
    catalog[0]["open_weights"] = False
    cfg = ProductConfig(**_prod_services(tmp_path), model_catalog_json=json.dumps(catalog), quality_snapshots_json=json.dumps(_snapshots(catalog)))
    with pytest.raises(RuntimeError, match="not open-weight"):
        cfg.validate()


def test_production_rejects_open_weight_without_commercial_rights(tmp_path):
    import json
    catalog = _full_open_weight_catalog()
    catalog[0]["commercial_use_allowed"] = False
    cfg = ProductConfig(**_prod_services(tmp_path), model_catalog_json=json.dumps(catalog), quality_snapshots_json=json.dumps(_snapshots(catalog)))
    with pytest.raises(RuntimeError, match="lacks commercial-use rights"):
        cfg.validate()


def test_production_requires_pinned_weight_and_license_metadata(tmp_path):
    import json
    catalog = _full_open_weight_catalog()
    catalog[0]["weights_uri"] = None
    cfg = ProductConfig(**_prod_services(tmp_path), model_catalog_json=json.dumps(catalog), quality_snapshots_json=json.dumps(_snapshots(catalog)))
    with pytest.raises(RuntimeError, match="pin downloadable weights"):
        cfg.validate()


def test_operator_admin_cli_is_packaged():
    import vaak.admin
    assert callable(vaak.admin.main)


def test_same_tenant_subject_cannot_laterally_access_controlled_resource(tmp_path):
    auth = TenantAuthStore(str(tmp_path / "auth-controller.db"))
    ta = auth.issue_token("tenant-x", "alice", ("voice:enroll",))
    tb = auth.issue_token("tenant-x", "bob", ("voice:enroll",))
    a = auth.authenticate(ta); b = auth.authenticate(tb)
    auth.bind_resource("tenant-x", "principal", "p-alice", "alice")
    auth.require_resource(a, "principal", "p-alice", "voice:enroll")
    with pytest.raises(Exception, match="not controlled"):
        auth.require_resource(b, "principal", "p-alice", "voice:enroll")


def test_tenant_admin_can_access_same_tenant_controlled_resource(tmp_path):
    auth = TenantAuthStore(str(tmp_path / "auth-admin.db"))
    admin_token = auth.issue_token("tenant-x", "ops", ("voice:enroll", "tenant:admin"))
    auth.bind_resource("tenant-x", "principal", "p-alice", "alice")
    auth.require_resource(auth.authenticate(admin_token), "principal", "p-alice", "voice:enroll")
