"""Contract tests for the vaakd governance core."""
from __future__ import annotations

import base64
import json
import threading
import time
import urllib.request

import pytest

from vaak.daemon import VaakCore, make_handler
from vaak.envelope import (
    ChannelClass,
    EnvelopeEnforcer,
    EnvelopeError,
    VoiceVerb,
    sign_envelope,
)
from vaak.identity import SubjectKeypair, CredentialError
from vaak.session import SessionError
from vaak.vault import VaultError
from vaak import watermark
from http.server import ThreadingHTTPServer

MODEL = b"open-weight-voice-model-bytes-for-testing" * 8


# ---------------------------------------------------------------- helpers
def enrolled_core(twin="twin-1", langs=("en",)):
    core = VaakCore()
    s = core.enroll_open(twin)
    core.enroll_complete(twin, {
        "session_id": s["session_id"],
        "voice_model_b64": base64.b64encode(MODEL).decode(),
        "spoken_challenge": s["challenge"],
        "languages": list(langs),
    })
    return core


def with_envelope(core, twin="twin-1", granted=None, hc=None):
    core.set_envelope(twin, {
        "version": 1,
        "granted": granted or ["speak_private", "speak_public",
                               "telephony_speak", "broadcast"],
        "high_consequence": hc or [],
    })
    return core


# ---------------------------------------------------------------- enrollment
def test_enrollment_requires_matching_challenge():
    core = VaakCore()
    s = core.enroll_open("t")
    with pytest.raises(Exception, match="challenge mismatch"):
        core.enroll_complete("t", {
            "session_id": s["session_id"],
            "voice_model_b64": base64.b64encode(MODEL).decode(),
            "spoken_challenge": "a replayed recording of something else",
        })


def test_enrollment_session_single_use():
    core = VaakCore()
    s = core.enroll_open("t")
    ok = {
        "session_id": s["session_id"],
        "voice_model_b64": base64.b64encode(MODEL).decode(),
        "spoken_challenge": s["challenge"],
    }
    core.enroll_complete("t", ok)
    with pytest.raises(Exception, match="no such enrollment session"):
        core.enroll_complete("t", ok)


# ---------------------------------------------------------------- envelope
def test_deny_by_default():
    owner = SubjectKeypair()
    env = sign_envelope("t", 1, {VoiceVerb.SPEAK_PRIVATE}, owner)
    enf = EnvelopeEnforcer(env)
    with pytest.raises(EnvelopeError, match="not granted"):
        enf.authorize(VoiceVerb.SPEAK_PUBLIC, ChannelClass.LOCAL)


def test_verb_channel_structure():
    owner = SubjectKeypair()
    env = sign_envelope("t", 1, set(VoiceVerb), owner)
    enf = EnvelopeEnforcer(env)
    with pytest.raises(EnvelopeError, match="not valid on channel"):
        enf.authorize(VoiceVerb.BROADCAST, ChannelClass.CALL)


def test_high_consequence_requires_presence():
    owner = SubjectKeypair()
    env = sign_envelope(
        "t", 1, {VoiceVerb.BROADCAST}, owner,
        high_consequence={VoiceVerb.BROADCAST},
    )
    enf = EnvelopeEnforcer(env)
    with pytest.raises(EnvelopeError, match="presence check"):
        enf.authorize(VoiceVerb.BROADCAST, ChannelClass.STREAM)
    auth = enf.authorize(VoiceVerb.BROADCAST, ChannelClass.STREAM,
                         presence_checked=True)
    assert auth.presence_checked


# ---------------------------------------------------------------- sessions
def test_full_speak_path_watermarks_and_manifests():
    core = with_envelope(enrolled_core())
    sid = core.open_session("twin-1", {
        "verb": "speak_private", "channel": "local",
    })["session_id"]
    pcm = core.speak(sid, {"text": "hello sovereign world"})
    assert len(pcm) > 0
    # Watermark verifiable via derived detection key:
    consent = core.enrollment.consent_for(core.assets["twin-1"])
    dk = core.vault.detection_key(consent.voice_model_hash)
    r = watermark.detect(pcm, dk)
    assert r.present and r.confidence == 1.0
    # Wrong key: no detection.
    assert not watermark.detect(pcm, b"\x00" * 32).present
    m = core.close_session(sid)
    assert m["frames_emitted"] > 0
    assert core.log.verify_chain("twin-1")


def test_call_channel_requires_disclosure_before_content():
    core = with_envelope(enrolled_core())
    sid = core.open_session("twin-1", {
        "verb": "telephony_speak", "channel": "call",
    })["session_id"]
    with pytest.raises(SessionError, match="disclosure required"):
        core.speak(sid, {"text": "hi"})
    core.sessions[sid].disclose()
    assert len(core.speak(sid, {"text": "hi"})) > 0
    m = core.close_session(sid)
    assert m["disclosed"] is True


def test_language_gating():
    core = with_envelope(enrolled_core(langs=("en",)))
    with pytest.raises(SessionError, match="not enabled by owner"):
        core.open_session("twin-1", {
            "verb": "speak_private", "channel": "local", "language": "fr",
        })
    # Owner enables French explicitly:
    core.enrollment.enable_language(
        core.assets["twin-1"], "fr", core.owner_for("twin-1"))
    sid = core.open_session("twin-1", {
        "verb": "speak_private", "channel": "local", "language": "fr",
    })["session_id"]
    assert sid


def test_barge_in_halts_stream():
    core = with_envelope(enrolled_core())
    sid = core.open_session("twin-1", {
        "verb": "speak_private", "channel": "local",
    })["session_id"]
    session = core.sessions[sid]
    chunks = []
    gen = session.speak("one two three four five six seven eight")
    chunks.append(next(gen))
    session.barge_in()
    chunks.extend(gen)  # generator must stop within one chunk
    assert len(chunks) == 1


def test_revoked_credential_cannot_open_session():
    core = with_envelope(enrolled_core())
    # Revoke everything the intermediate would issue by revoking post-issue:
    sid = core.open_session("twin-1", {
        "verb": "speak_private", "channel": "local",
    })["session_id"]
    cred = core.sessions[sid]._credential
    core.intermediate.revoke(cred.credential_id)
    with pytest.raises(CredentialError, match="revoked"):
        core.intermediate.verify(cred)


# ---------------------------------------------------------------- erasure
def test_crypto_shred_kills_synthesis_but_not_detection():
    core = with_envelope(enrolled_core())
    sid = core.open_session("twin-1", {
        "verb": "speak_private", "channel": "local",
    })["session_id"]
    pcm = core.speak(sid, {"text": "before erasure"})
    core.close_session(sid)
    consent = core.enrollment.consent_for(core.assets["twin-1"])
    result = core.erase("twin-1")
    assert result["shredded"] and result["detection_still_available"]
    # New sessions are refused before a fresh credential can be minted.
    with pytest.raises(VaultError, match="voice erased"):
        core.open_session("twin-1", {
            "verb": "speak_private", "channel": "local",
        })
    # Previously issued audio still verifiable:
    dk = core.vault.detection_key(consent.voice_model_hash)
    assert watermark.detect(pcm, dk).present


def test_erasure_atomically_revokes_credentials_and_kills_live_attestation():
    core = with_envelope(enrolled_core())
    sid = core.open_session("twin-1", {
        "verb": "speak_private", "channel": "local",
    })["session_id"]
    session = core.sessions[sid]
    cred = session._credential
    assert session.attest("before")["statement"]["credential_id"] == cred.credential_id

    result = core.erase("twin-1")
    assert cred.credential_id in result["revoked_credential_ids"]
    assert sid in result["terminated_session_ids"]
    assert core.intermediate.is_revoked(cred.credential_id)
    assert sid not in core.sessions

    with pytest.raises(SessionError, match="unknown session"):
        core.attest_session(sid, {"challenge_nonce": "after"})


def test_erasure_revokes_all_credentials_ever_issued_for_voice_model():
    core = with_envelope(enrolled_core())
    issued = []
    for _ in range(3):
        sid = core.open_session("twin-1", {
            "verb": "speak_private", "channel": "local",
        })["session_id"]
        issued.append(core.sessions[sid]._credential.credential_id)
        core.close_session(sid)
    result = core.erase("twin-1")
    assert set(issued).issubset(set(result["revoked_credential_ids"]))
    assert all(core.intermediate.is_revoked(cid) for cid in issued)


# ---------------------------------------------------------------- provenance
def test_manifest_chain_and_anchoring():
    core = with_envelope(enrolled_core())
    hashes = []
    for text in ("one", "two", "three"):
        sid = core.open_session("twin-1", {
            "verb": "speak_private", "channel": "local",
        })["session_id"]
        core.speak(sid, {"text": text})
        hashes.append(core.close_session(sid)["manifest_hash"])
    a = core.anchor()
    assert a["count"] == 4  # 3 manifests + 1 anchored birth record
    for h in hashes:
        assert core.log.verify_inclusion(h)
    assert core.log.verify_chain("twin-1")


# ---------------------------------------------------------------- HTTP e2e
def test_http_end_to_end():
    core = VaakCore()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(core))
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{port}"

    def post(path, body=None, raw=False):
        req = urllib.request.Request(
            base + path, data=json.dumps(body or {}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req) as r:
            data = r.read()
            return data if raw else json.loads(data)

    try:
        s = post("/twins/t1/enroll/open")
        post("/twins/t1/enroll/complete", {
            "session_id": s["session_id"],
            "voice_model_b64": base64.b64encode(MODEL).decode(),
            "spoken_challenge": s["challenge"],
        })
        post("/twins/t1/envelope", {"version": 1, "granted": ["speak_private"]})
        sid = post("/twins/t1/sessions", {
            "verb": "speak_private", "channel": "local"})["session_id"]
        pcm = post(f"/sessions/{sid}/speak", {"text": "arena ready"}, raw=True)
        assert len(pcm) > 0
        manifest = post(f"/sessions/{sid}/close")
        assert manifest["frames_emitted"] > 0
        verify = post("/verify/audio", {
            "audio_b64": base64.b64encode(pcm).decode(),
            "voice_model_hash": manifest and core.enrollment.consent_for(
                core.assets["t1"]).voice_model_hash,
        })
        assert verify["present"] is True
        anchored = post("/anchor")
        assert anchored["count"] == 2  # manifest + birth record
    finally:
        httpd.shutdown()
