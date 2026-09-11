"""vaak.service — dual-mode facade over the Vaak governance core."""
from __future__ import annotations

import hashlib
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .daemon import VaakCore
from .envelope import ChannelClass, Register, VoiceVerb
from .agency import AgencyContext, AgencyPolicy, UndertoneObservation
from .provenance import UtteranceReceipt, VerificationTrustBundle
from .util import canon, sha256_hex
from . import watermark


class VaakService:
    """In-process API for external host applications, including Atma integrations."""

    def __init__(self, core: VaakCore | None = None):
        self.core = core or VaakCore()

    # -- lifecycle ---------------------------------------------------------
    def enroll(self, twin_id: str, model_bytes: bytes,
               languages: tuple[str, ...] = ("en",)) -> dict:
        """One-call enrollment for hosts that run their liveness ceremony."""
        import base64
        s = self.core.enroll_open(twin_id)
        return self.core.enroll_complete(twin_id, {
            "session_id": s["session_id"],
            "voice_model_b64": base64.b64encode(model_bytes).decode(),
            "spoken_challenge": s["challenge"],
            "languages": list(languages),
        })

    def set_envelope(self, twin_id: str, *, version: int,
                     granted: set[VoiceVerb],
                     registers: set[Register] | None = None,
                     high_consequence: set[VoiceVerb] | None = None,
                     disclose_on_calls: bool = True,
                     agency_policy: AgencyPolicy | None = None) -> dict:
        return self.core.set_envelope(twin_id, {
            "version": version,
            "granted": [v.value for v in granted],
            "registers": [r.value for r in (registers or {Register.PRIVATE})],
            "high_consequence": [v.value for v in (high_consequence or set())],
            "disclose_on_calls": disclose_on_calls,
            "agency_policy": (agency_policy or AgencyPolicy()).body(),
        })

    def erase(self, twin_id: str) -> dict:
        return self.core.erase(twin_id)

    # -- speaking ----------------------------------------------------------
    def open_session(self, twin_id: str, verb: VoiceVerb,
                     channel: ChannelClass,
                     register: Register = Register.PRIVATE,
                     language: str = "en",
                     presence_checked: bool = False) -> str:
        return self.core.open_session(twin_id, {
            "verb": verb.value, "channel": channel.value,
            "register": register.value, "language": language,
            "presence_checked": presence_checked,
        })["session_id"]

    def speak(self, session_id: str, text: str,
              prosody: dict | None = None,
              agency: AgencyContext | dict | None = None) -> bytes:
        body = {"text": text, "prosody": prosody}
        if isinstance(agency, AgencyContext):
            body["agency"] = agency.body()
        elif agency is not None:
            body["agency"] = agency
        return self.core.speak(session_id, body)

    def observe_incoming_undertone(
        self, session_id: str, observation: UndertoneObservation | dict
    ) -> dict:
        body = observation.body() if isinstance(observation, UndertoneObservation) else observation
        return self.core.observe_incoming_undertone(session_id, body)

    def record_recipient_refusal(self, session_id: str) -> dict:
        return self.core.record_recipient_refusal(session_id)

    def record_material_change(self, session_id: str, evidence_ref: str) -> dict:
        """Trusted host signal; not available on the public reference daemon."""
        return self.core.record_material_change(session_id, evidence_ref)

    def agency_report(self, session_id: str) -> dict:
        return self.core.agency_report(session_id)

    def attest(self, session_id: str, challenge_nonce: str) -> dict:
        return self.core.attest_session(
            session_id, {"challenge_nonce": challenge_nonce}
        )

    def close(self, session_id: str) -> dict:
        return self.core.close_session(session_id)

    # -- proof surface -----------------------------------------------------
    def resolve_clip(self, pcm: bytes) -> dict:
        return self.core.resolve_clip(pcm)

    def receipt(self, manifest_hash: str) -> UtteranceReceipt:
        return self.core.log.receipt(manifest_hash)

    def verification_trust_bundle(self, twin_id: str) -> dict:
        """Reference public keys that a verifier must pin independently."""
        return self.core.verification_trust_bundle(twin_id)

    @staticmethod
    def verify_receipt(
        receipt: UtteranceReceipt,
        trust: VerificationTrustBundle | dict,
    ) -> bool:
        return UtteranceReceipt.verify(receipt, trust)

    @staticmethod
    def verify_clip_proof(
        pcm: bytes,
        receipt: UtteranceReceipt,
        trust: VerificationTrustBundle | dict,
    ) -> bool:
        if not UtteranceReceipt.verify(receipt, trust):
            return False
        manifest = receipt.manifest
        if sha256_hex(pcm) != manifest.get("content_digest"):
            return False
        beacon = watermark.extract_beacon(pcm)
        if beacon is None:
            return False
        expected = hashlib.sha256(
            f"{manifest['twin_id']}|{manifest['session_id']}".encode()
        ).digest()[: watermark.BEACON_BYTES]
        return beacon == expected

    @staticmethod
    def verify_attestation(attestation: dict, trust: VerificationTrustBundle | dict) -> bool:
        """Verify a live attestation against independently pinned identity keys.

        The reference credential-status statement is issuer-signed at the same
        timestamp as the attestation.  Production maps that status proof to
        the Crucible/Chrysalis revocation service.
        """
        trust = VerificationTrustBundle.from_value(trust)
        statement = dict(attestation.get("statement") or {})
        credential = dict(attestation.get("credential") or {})
        envelope = dict(attestation.get("envelope") or {})
        status = dict(attestation.get("credential_status") or {})
        signature = attestation.get("signature")
        cred_sig = credential.pop("issuer_signature", None)
        env_sig = envelope.pop("owner_signature", None)
        status_statement = dict(status.get("statement") or {})
        status_sig = status.get("issuer_signature")
        if not all((signature, cred_sig, env_sig, status_sig)):
            return False
        try:
            issuer: Ed25519PublicKey = serialization.load_pem_public_key(
                trust.issuer_public_key_pem.encode()
            )
            issuer.verify(bytes.fromhex(cred_sig), canon(credential))
            owner: Ed25519PublicKey = serialization.load_pem_public_key(
                trust.owner_public_key_pem.encode()
            )
            owner.verify(bytes.fromhex(env_sig), canon(envelope))
            subject: Ed25519PublicKey = serialization.load_pem_public_key(
                credential["public_key_pem"].encode()
            )
            subject.verify(bytes.fromhex(signature), canon(statement))
            issuer.verify(bytes.fromhex(status_sig), canon(status_statement))
        except (InvalidSignature, ValueError, TypeError, KeyError, AttributeError):
            return False

        if statement.get("type") != "vaak-live-attestation-v1":
            return False
        if credential.get("credential_id") != statement.get("credential_id"):
            return False
        if credential.get("twin_id") != statement.get("twin_id"):
            return False
        if sha256_hex(canon(envelope)) != statement.get("envelope_hash"):
            return False
        if sha256_hex(canon(envelope.get("agency_policy", {}))) != statement.get("agency_policy_hash"):
            return False
        if status_statement.get("type") != "vaak-credential-status-reference-v1":
            return False
        if status_statement.get("credential_id") != statement.get("credential_id"):
            return False
        if status_statement.get("status") != "valid":
            return False
        if status_statement.get("checked_at") != statement.get("credential_valid_at"):
            return False
        if status_statement.get("expires_at") != credential.get("expires_at"):
            return False
        return True
