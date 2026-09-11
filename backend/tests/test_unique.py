"""Tests for vaakd's distinctive capabilities (v0.2.1)."""
from __future__ import annotations

import base64
import secrets

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature

from vaak.daemon import VaakCore
from vaak.identity import CredentialError
from vaak.envelope import ChannelClass, EnvelopeError, Register, VoiceVerb
from vaak.provenance import UtteranceReceipt
from vaak.service import VaakService
from vaak.util import canon
from vaak import watermark

MODEL = b"open-weight-voice-model-bytes-for-testing" * 8


def svc(langs=("en",)) -> VaakService:
    s = VaakService()
    s.enroll("twin-1", MODEL, languages=langs)
    s.set_envelope(
        "twin-1", version=1,
        granted={VoiceVerb.SPEAK_PRIVATE, VoiceVerb.SPEAK_PUBLIC,
                 VoiceVerb.TELEPHONY_SPEAK, VoiceVerb.BROADCAST},
        registers={Register.PRIVATE, Register.BOARD},
    )
    return s


# ------------------------------------------------- self-attesting audio
def test_clip_resolves_to_its_own_session():
    s = svc()
    sid = s.open_session("twin-1", VoiceVerb.SPEAK_PRIVATE, ChannelClass.LOCAL)
    pcm = s.speak(sid, "the clip carries its own provenance")
    manifest = s.close(sid)
    s.core.anchor()
    r = s.resolve_clip(pcm)
    assert r["resolved"] and r["authentic"]
    assert r["session_id"] == manifest["session_id"]
    assert r["manifest_hash"] == manifest["manifest_hash"]
    assert "receipt" in r  # anchored → portable proof included


def test_foreign_audio_does_not_resolve():
    s = svc()
    r = s.resolve_clip(b"\x00\x01" * 4000)
    assert not r["resolved"]


def test_two_sessions_resolve_distinctly():
    s = svc()
    clips = {}
    for text in ("first", "second"):
        sid = s.open_session("twin-1", VoiceVerb.SPEAK_PRIVATE,
                             ChannelClass.LOCAL)
        clips[sid] = s.speak(sid, text)
        s.close(sid)
    for sid, pcm in clips.items():
        assert s.resolve_clip(pcm)["session_id"] == sid


# ------------------------------------------------- utterance receipts
def test_receipt_verifies_offline_and_detects_tamper():
    s = svc()
    sid = s.open_session("twin-1", VoiceVerb.SPEAK_PRIVATE, ChannelClass.LOCAL)
    s.speak(sid, "receipt me")
    m = s.close(sid)
    s.core.anchor()
    rec = s.receipt(m["manifest_hash"])
    # Offline verification: receipt + independently pinned anchor key.
    trust = s.verification_trust_bundle("twin-1")
    assert VaakService.verify_receipt(rec, trust)
    # Tamper with the manifest → verification fails.
    forged = UtteranceReceipt(
        manifest=dict(rec.manifest, verb="broadcast"),
        manifest_hash=rec.manifest_hash,
        proof=rec.proof, root=rec.root, anchored_at=rec.anchored_at,
        anchor_statement=rec.anchor_statement,
        anchor_signature=rec.anchor_signature,
        authorization_evidence=rec.authorization_evidence,
    )
    assert not VaakService.verify_receipt(forged, trust)


def test_receipts_verify_in_multi_manifest_batches():
    s = svc()
    hashes = []
    for text in ("a", "b", "c", "d", "e"):
        sid = s.open_session("twin-1", VoiceVerb.SPEAK_PRIVATE,
                             ChannelClass.LOCAL)
        s.speak(sid, text)
        hashes.append(s.close(sid)["manifest_hash"])
    s.core.anchor()
    trust = s.verification_trust_bundle("twin-1")
    for h in hashes:  # every position in the tree, odd batch size
        assert VaakService.verify_receipt(s.receipt(h), trust)


def test_receipt_rejects_forged_authorization_evidence():
    s = svc()
    sid = s.open_session("twin-1", VoiceVerb.SPEAK_PRIVATE, ChannelClass.LOCAL)
    s.speak(sid, "authorization is part of the proof")
    m = s.close(sid)
    s.core.anchor()
    rec = s.receipt(m["manifest_hash"])
    trust = s.verification_trust_bundle("twin-1")
    forged_evidence = dict(rec.authorization_evidence)
    forged_envelope = dict(forged_evidence["envelope"])
    forged_envelope["granted"] = ["broadcast"]
    forged_evidence["envelope"] = forged_envelope
    forged = UtteranceReceipt(
        manifest=rec.manifest, manifest_hash=rec.manifest_hash,
        proof=rec.proof, root=rec.root, anchored_at=rec.anchored_at,
        anchor_statement=rec.anchor_statement,
        anchor_signature=rec.anchor_signature,
        authorization_evidence=forged_evidence,
    )
    assert not VaakService.verify_receipt(forged, trust)


def test_receipt_requires_independently_trusted_anchor_key():
    s = svc()
    sid = s.open_session("twin-1", VoiceVerb.SPEAK_PRIVATE, ChannelClass.LOCAL)
    s.speak(sid, "trust roots are not self-authenticating")
    m = s.close(sid)
    s.core.anchor()
    rec = s.receipt(m["manifest_hash"])
    other_s = svc()
    other_s.enroll("twin-1", MODEL)
    other = other_s.verification_trust_bundle("twin-1")
    assert not VaakService.verify_receipt(rec, other)


def test_exact_clip_proof_verifies_offline_and_binds_audio():
    s = svc()
    sid = s.open_session("twin-1", VoiceVerb.SPEAK_PRIVATE, ChannelClass.LOCAL)
    pcm = s.speak(sid, "public exact-content proof")
    m = s.close(sid)
    s.core.anchor()
    rec = s.receipt(m["manifest_hash"])
    trust = s.verification_trust_bundle("twin-1")
    assert VaakService.verify_clip_proof(pcm, rec, trust)
    tampered = bytearray(pcm)
    tampered[-1] ^= 0x01
    assert not VaakService.verify_clip_proof(bytes(tampered), rec, trust)


# ------------------------------------------------- live attestation
def _cred_pubkey(att):
    return serialization.load_pem_public_key(
        att["credential"]["public_key_pem"].encode())


def test_live_attestation_binds_nonce_and_verifies():
    s = svc()
    sid = s.open_session("twin-1", VoiceVerb.TELEPHONY_SPEAK,
                         ChannelClass.CALL)
    s.core.sessions[sid].disclose()
    s.speak(sid, "hello counterparty")
    nonce = secrets.token_hex(8)
    att = s.attest(sid, nonce)
    assert att["statement"]["challenge_nonce"] == nonce
    # Counterparty verification: session key signature checks out...
    _cred_pubkey(att).verify(
        bytes.fromhex(att["signature"]), canon(att["statement"]))
    # ...and the credential chains to the intermediate.
    from vaak.identity import LeafCredential
    cred = LeafCredential(**att["credential"])
    s.core.intermediate.verify(cred)


def test_attestation_signature_rejects_forgery():
    s = svc()
    sid = s.open_session("twin-1", VoiceVerb.SPEAK_PRIVATE,
                         ChannelClass.LOCAL)
    att = s.attest(sid, "nonce")
    forged = dict(att["statement"], twin_id="someone-else")
    with pytest.raises(InvalidSignature):
        _cred_pubkey(att).verify(bytes.fromhex(att["signature"]), canon(forged))


def test_revoked_credential_cannot_attest_mid_session():
    s = svc()
    sid = s.open_session("twin-1", VoiceVerb.SPEAK_PRIVATE, ChannelClass.LOCAL)
    cred = s.core.sessions[sid]._credential
    s.core.intermediate.revoke(cred.credential_id)
    with pytest.raises(CredentialError, match="revoked"):
        s.attest(sid, "fresh-nonce")


def test_revoked_credential_cannot_emit_more_audio_mid_session():
    s = svc()
    sid = s.open_session("twin-1", VoiceVerb.SPEAK_PRIVATE, ChannelClass.LOCAL)
    assert s.speak(sid, "before revoke")
    cred = s.core.sessions[sid]._credential
    s.core.intermediate.revoke(cred.credential_id)
    with pytest.raises(CredentialError, match="revoked"):
        s.speak(sid, "after revoke")


# ------------------------------------------------- registers
def test_ungranted_register_is_refused():
    s = svc()  # PRIVATE and BOARD granted; STAGE is not
    with pytest.raises(EnvelopeError, match="register 'stage' not granted"):
        s.open_session("twin-1", VoiceVerb.BROADCAST, ChannelClass.STREAM,
                       register=Register.STAGE)


def test_register_recorded_in_manifest():
    s = svc()
    sid = s.open_session("twin-1", VoiceVerb.SPEAK_PRIVATE,
                         ChannelClass.LOCAL, register=Register.BOARD)
    s.speak(sid, "quarterly numbers")
    assert s.close(sid)["register"] == "board"


# ------------------------------------------------- status registry
def test_formerly_authorized_after_erasure():
    s = svc()
    sid = s.open_session("twin-1", VoiceVerb.SPEAK_PRIVATE, ChannelClass.LOCAL)
    pcm = s.speak(sid, "spoken before erasure")
    s.close(sid)
    consent = s.core.enrollment.consent_for(s.core.assets["twin-1"])
    vm_hash = consent.voice_model_hash
    # Before: authorized.
    v = s.core.verify_audio({
        "audio_b64": base64.b64encode(pcm).decode(),
        "voice_model_hash": vm_hash})
    assert v["status"] == "authorized"
    # Erase → formerly_authorized with a death certificate.
    result = s.erase("twin-1")
    assert result["death_certificate_hash"]
    v2 = s.core.verify_audio({
        "audio_b64": base64.b64encode(pcm).decode(),
        "voice_model_hash": vm_hash})
    assert v2["present"] is True  # detection survives shred
    assert v2["status"] == "formerly_authorized"
    assert v2["death_certificate_hash"] == result["death_certificate_hash"]


def test_unwatermarked_audio_is_not_ours():
    s = svc()
    consent = s.core.enrollment.consent_for(s.core.assets["twin-1"])
    v = s.core.verify_audio({
        "audio_b64": base64.b64encode(b"\x00\x02" * 4000).decode(),
        "voice_model_hash": consent.voice_model_hash})
    assert v["status"] == "not_ours"


# ------------------------------------------------- dual-mode
def test_embedded_service_shares_core_with_daemon_surface():
    core = VaakCore()
    embedded = VaakService(core)  # external host posture: in-process composition
    embedded.enroll("t", MODEL)
    embedded.set_envelope("t", version=1, granted={VoiceVerb.SPEAK_PRIVATE})
    sid = embedded.open_session("t", VoiceVerb.SPEAK_PRIVATE,
                                ChannelClass.LOCAL)
    pcm = embedded.speak(sid, "one core, two postures")
    embedded.close(sid)
    core.anchor()
    # The daemon-surface core sees exactly what the embedded facade did:
    assert core.log.verify_chain("t")
    assert core.resolve_clip(pcm)["resolved"]


# ------------------------------------------------- lifecycle anchoring
def test_birth_and_death_records_are_anchored():
    s = svc()
    consent = s.core.enrollment.consent_for(s.core.assets["twin-1"])
    result = s.erase("twin-1")
    a = s.core.anchor()
    assert a["count"] >= 2
    # Merkle inclusion is the real anchoring check in the reference path:
    assert s.core.log.verify_inclusion(consent.record_hash())
    assert s.core.log.verify_inclusion(result["death_certificate_hash"])
