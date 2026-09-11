from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Protocol

from ..util import canon, sha256_hex


class EvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class PresenceEvidence:
    evidence_id: str
    tenant_id: str
    principal_id: str
    method: str
    issued_at: float
    expires_at: float
    assertion: dict
    signature: str

    def unsigned_body(self) -> dict:
        return {
            "type": "vaak-presence-evidence-v1",
            "evidence_id": self.evidence_id,
            "tenant_id": self.tenant_id,
            "principal_id": self.principal_id,
            "method": self.method,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "assertion": self.assertion,
        }

    def evidence_hash(self) -> str:
        return sha256_hex(canon(self.unsigned_body() | {"signature": self.signature}))


@dataclass(frozen=True)
class DisclosureEvidence:
    evidence_id: str
    tenant_id: str
    principal_id: str
    session_id: str
    channel: str
    text: str
    audio_sha256: str
    delivered_at: float
    transport_ref: str
    signature: str

    def unsigned_body(self) -> dict:
        return {
            "type": "vaak-disclosure-evidence-v1",
            "evidence_id": self.evidence_id,
            "tenant_id": self.tenant_id,
            "principal_id": self.principal_id,
            "session_id": self.session_id,
            "channel": self.channel,
            "text": self.text,
            "audio_sha256": self.audio_sha256,
            "delivered_at": self.delivered_at,
            "transport_ref": self.transport_ref,
        }

    def evidence_hash(self) -> str:
        return sha256_hex(canon(self.unsigned_body() | {"signature": self.signature}))


class PresenceVerifier(Protocol):
    def verify(self, token: str, *, tenant_id: str, principal_id: str) -> PresenceEvidence: ...


class DisclosureVerifier(Protocol):
    def verify_delivery(self, token: str, *, tenant_id: str, principal_id: str, session_id: str, channel: str, text: str, audio_sha256: str) -> DisclosureEvidence: ...


class HMACPresenceVerifier:
    """Deterministic signed-evidence authority for tests/private deployments.

    Production should source this secret from an HSM/secret manager or replace
    the verifier with an external identity/presence service.
    """
    def __init__(self, secret: bytes):
        if len(secret) < 32:
            raise ValueError("presence secret must be at least 32 bytes")
        self.secret = secret

    def mint(self, *, tenant_id: str, principal_id: str, method: str = "owner_presence", ttl_seconds: float = 300.0, assertion: dict | None = None) -> str:
        now = time.time()
        body = {
            "evidence_id": "pres_" + hashlib.sha256(f"{tenant_id}|{principal_id}|{now}".encode()).hexdigest()[:20],
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "method": method,
            "issued_at": now,
            "expires_at": now + ttl_seconds,
            "assertion": assertion or {},
        }
        sig = hmac.new(self.secret, canon(body), hashlib.sha256).hexdigest()
        return base64.urlsafe_b64encode(json.dumps(body | {"signature": sig}, separators=(",", ":")).encode()).decode()

    def verify(self, token: str, *, tenant_id: str, principal_id: str) -> PresenceEvidence:
        try:
            obj = json.loads(base64.urlsafe_b64decode(token.encode()))
        except Exception as exc:
            raise EvidenceError("invalid presence evidence encoding") from exc
        sig = str(obj.pop("signature", ""))
        expected = hmac.new(self.secret, canon(obj), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            raise EvidenceError("invalid presence evidence signature")
        if obj.get("tenant_id") != tenant_id or obj.get("principal_id") != principal_id:
            raise EvidenceError("presence evidence subject mismatch")
        if time.time() >= float(obj.get("expires_at", 0)):
            raise EvidenceError("presence evidence expired")
        return PresenceEvidence(signature=sig, **obj)


class HMACDisclosureVerifier:
    def __init__(self, secret: bytes):
        if len(secret) < 32:
            raise ValueError("disclosure secret must be at least 32 bytes")
        self.secret = secret

    def mint(self, *, tenant_id: str, principal_id: str, session_id: str, channel: str, text: str, audio_sha256: str, transport_ref: str = "test-transport") -> str:
        now = time.time()
        body = {
            "evidence_id": "disc_" + hashlib.sha256(f"{session_id}|{audio_sha256}|{now}".encode()).hexdigest()[:20],
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "session_id": session_id,
            "channel": channel,
            "text": text,
            "audio_sha256": audio_sha256,
            "delivered_at": now,
            "transport_ref": transport_ref,
        }
        sig = hmac.new(self.secret, canon(body), hashlib.sha256).hexdigest()
        return base64.urlsafe_b64encode(json.dumps(body | {"signature": sig}, separators=(",", ":")).encode()).decode()

    def verify_delivery(self, token: str, *, tenant_id: str, principal_id: str, session_id: str, channel: str, text: str, audio_sha256: str) -> DisclosureEvidence:
        try:
            obj = json.loads(base64.urlsafe_b64decode(token.encode()))
        except Exception as exc:
            raise EvidenceError("invalid disclosure evidence encoding") from exc
        sig = str(obj.pop("signature", ""))
        expected = hmac.new(self.secret, canon(obj), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            raise EvidenceError("invalid disclosure evidence signature")
        expected_fields = {
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "session_id": session_id,
            "channel": channel,
            "text": text,
            "audio_sha256": audio_sha256,
        }
        if any(obj.get(k) != v for k, v in expected_fields.items()):
            raise EvidenceError("disclosure evidence does not match rendered disclosure")
        return DisclosureEvidence(signature=sig, **obj)


@dataclass(frozen=True)
class EnrollmentEvidence:
    principal_id: str
    tenant_id: str
    challenge: str
    challenge_detected: bool
    liveness_passed: bool
    speaker_match_passed: bool
    anti_spoof_passed: bool
    verifier: str
    checked_at: float
    evidence_ref: str | None = None

    def body(self) -> dict:
        return {
            "type": "vaak-enrollment-evidence-v1",
            "principal_id": self.principal_id,
            "tenant_id": self.tenant_id,
            "challenge": self.challenge,
            "challenge_detected": self.challenge_detected,
            "liveness_passed": self.liveness_passed,
            "speaker_match_passed": self.speaker_match_passed,
            "anti_spoof_passed": self.anti_spoof_passed,
            "verifier": self.verifier,
            "checked_at": self.checked_at,
            "evidence_ref": self.evidence_ref,
        }

    def require_pass(self) -> None:
        if not all((self.challenge_detected, self.liveness_passed, self.speaker_match_passed, self.anti_spoof_passed)):
            raise EvidenceError("voice enrollment verification failed")


class EnrollmentVerifier(Protocol):
    def verify(self, pcm16: bytes, *, tenant_id: str, principal_id: str, expected_challenge: str, claimed_challenge: str, sample_rate: int = 16000) -> EnrollmentEvidence: ...


class DevEnrollmentVerifier:
    """Contract test verifier. It is not biometric liveness."""
    def verify(self, pcm16: bytes, *, tenant_id: str, principal_id: str, expected_challenge: str, claimed_challenge: str, sample_rate: int = 16000) -> EnrollmentEvidence:
        del sample_rate
        ok = bool(pcm16) and claimed_challenge == expected_challenge
        evidence = EnrollmentEvidence(
            principal_id=principal_id,
            tenant_id=tenant_id,
            challenge=expected_challenge,
            challenge_detected=ok,
            liveness_passed=ok,
            speaker_match_passed=ok,
            anti_spoof_passed=ok,
            verifier="dev-contract-only",
            checked_at=time.time(),
        )
        evidence.require_pass()
        return evidence


@dataclass(frozen=True)
class HTTPEnrollmentVerifierConfig:
    base_url: str
    api_key: str | None = None
    timeout_s: float = 15.0


class HTTPEnrollmentVerifier:
    def __init__(self, config: HTTPEnrollmentVerifierConfig):
        self.config = config

    def verify(self, pcm16: bytes, *, tenant_id: str, principal_id: str, expected_challenge: str, claimed_challenge: str, sample_rate: int = 16000) -> EnrollmentEvidence:
        import httpx
        headers = {"Authorization": f"Bearer {self.config.api_key}"} if self.config.api_key else {}
        payload = {
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "expected_challenge": expected_challenge,
            "claimed_challenge": claimed_challenge,
            "sample_rate": sample_rate,
            "audio_b64": base64.b64encode(pcm16).decode(),
        }
        with httpx.Client(timeout=self.config.timeout_s) as client:
            r = client.post(f"{self.config.base_url.rstrip('/')}/v1/enrollment/verify", json=payload, headers=headers)
            r.raise_for_status()
            obj = r.json()
        evidence = EnrollmentEvidence(
            principal_id=principal_id,
            tenant_id=tenant_id,
            challenge=expected_challenge,
            challenge_detected=bool(obj.get("challenge_detected", False)),
            liveness_passed=bool(obj.get("liveness_passed", False)),
            speaker_match_passed=bool(obj.get("speaker_match_passed", False)),
            anti_spoof_passed=bool(obj.get("anti_spoof_passed", False)),
            verifier=str(obj.get("verifier", "enrollment-http")),
            checked_at=float(obj.get("checked_at", time.time())),
            evidence_ref=obj.get("evidence_ref"),
        )
        evidence.require_pass()
        return evidence


@dataclass(frozen=True)
class HTTPEvidenceConfig:
    base_url: str
    api_key: str | None = None
    timeout_s: float = 8.0


class HTTPPresenceVerifier:
    def __init__(self, config: HTTPEvidenceConfig):
        self.config = config

    def verify(self, token: str, *, tenant_id: str, principal_id: str) -> PresenceEvidence:
        import httpx
        headers = {"Authorization": f"Bearer {self.config.api_key}"} if self.config.api_key else {}
        with httpx.Client(timeout=self.config.timeout_s) as client:
            r = client.post(
                f"{self.config.base_url.rstrip('/')}/v1/presence/verify",
                json={"evidence": token, "tenant_id": tenant_id, "principal_id": principal_id},
                headers=headers,
            )
            r.raise_for_status()
            obj = r.json()
        evidence = PresenceEvidence(**obj)
        if evidence.tenant_id != tenant_id or evidence.principal_id != principal_id:
            raise EvidenceError("presence evidence subject mismatch")
        if time.time() >= evidence.expires_at:
            raise EvidenceError("presence evidence expired")
        return evidence


class HTTPDisclosureVerifier:
    def __init__(self, config: HTTPEvidenceConfig):
        self.config = config

    def verify_delivery(self, token: str, *, tenant_id: str, principal_id: str, session_id: str, channel: str, text: str, audio_sha256: str) -> DisclosureEvidence:
        import httpx
        headers = {"Authorization": f"Bearer {self.config.api_key}"} if self.config.api_key else {}
        payload = {
            "evidence": token,
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "session_id": session_id,
            "channel": channel,
            "text": text,
            "audio_sha256": audio_sha256,
        }
        with httpx.Client(timeout=self.config.timeout_s) as client:
            r = client.post(f"{self.config.base_url.rstrip('/')}/v1/disclosure/verify", json=payload, headers=headers)
            r.raise_for_status()
            obj = r.json()
        evidence = DisclosureEvidence(**obj)
        expected = (tenant_id, principal_id, session_id, channel, text, audio_sha256)
        actual = (evidence.tenant_id, evidence.principal_id, evidence.session_id, evidence.channel, evidence.text, evidence.audio_sha256)
        if actual != expected:
            raise EvidenceError("disclosure evidence does not match rendered disclosure")
        return evidence

@dataclass(frozen=True)
class IdentityEvidence:
    tenant_id: str
    subject_id: str
    role: str
    purpose: str
    verifier: str
    checked_at: float
    expires_at: float
    evidence_ref: str | None = None
    signature: str | None = None

    def body(self) -> dict:
        return {
            "type": "vaak-identity-evidence-v1",
            "tenant_id": self.tenant_id,
            "subject_id": self.subject_id,
            "role": self.role,
            "purpose": self.purpose,
            "verifier": self.verifier,
            "checked_at": self.checked_at,
            "expires_at": self.expires_at,
            "evidence_ref": self.evidence_ref,
            "signature": self.signature,
        }

    def evidence_hash(self) -> str:
        from ..util import canon, sha256_hex
        return sha256_hex(canon(self.body()))


class HMACIdentityVerifier:
    """Development identity-evidence authority; production uses HTTPIdentityVerifier."""
    def __init__(self, secret: bytes):
        self.secret = secret

    def mint(self, *, tenant_id: str, subject_id: str, role: str, purpose: str, ttl_seconds: float = 600.0) -> str:
        import hashlib, hmac, json
        now = time.time()
        body = {
            "tenant_id": tenant_id,
            "subject_id": subject_id,
            "role": role,
            "purpose": purpose,
            "verifier": "dev-hmac-identity",
            "checked_at": now,
            "expires_at": now + ttl_seconds,
            "evidence_ref": f"dev:{subject_id}:{int(now)}",
        }
        raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        sig = hmac.new(self.secret, raw, hashlib.sha256).hexdigest()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=") + "." + sig

    def verify(self, token: str, *, tenant_id: str, subject_id: str, role: str, purpose: str) -> IdentityEvidence:
        import hashlib, hmac, json
        try:
            enc, sig = token.split(".", 1)
            raw = base64.urlsafe_b64decode(enc + "=" * (-len(enc) % 4))
            obj = json.loads(raw)
        except Exception as exc:
            raise EvidenceError("malformed identity evidence") from exc
        if not hmac.compare_digest(sig, hmac.new(self.secret, raw, hashlib.sha256).hexdigest()):
            raise EvidenceError("invalid identity evidence signature")
        expected = (tenant_id, subject_id, role, purpose)
        actual = (obj.get("tenant_id"), obj.get("subject_id"), obj.get("role"), obj.get("purpose"))
        if actual != expected:
            raise EvidenceError("identity evidence subject/purpose mismatch")
        if time.time() >= float(obj.get("expires_at", 0)):
            raise EvidenceError("identity evidence expired")
        return IdentityEvidence(signature=sig, **obj)


class HTTPIdentityVerifier:
    """External authoritative identity/rights verification boundary."""
    def __init__(self, config: HTTPEvidenceConfig):
        self.config = config

    def verify(self, token: str, *, tenant_id: str, subject_id: str, role: str, purpose: str) -> IdentityEvidence:
        import httpx
        headers = {"Authorization": f"Bearer {self.config.api_key}"} if self.config.api_key else {}
        payload = {
            "evidence": token,
            "tenant_id": tenant_id,
            "subject_id": subject_id,
            "role": role,
            "purpose": purpose,
        }
        with httpx.Client(timeout=self.config.timeout_s) as client:
            r = client.post(f"{self.config.base_url.rstrip('/')}/v1/identity/verify", json=payload, headers=headers)
            r.raise_for_status()
            obj = r.json()
        ev = IdentityEvidence(**obj)
        if (ev.tenant_id, ev.subject_id, ev.role, ev.purpose) != (tenant_id, subject_id, role, purpose):
            raise EvidenceError("identity evidence subject/purpose mismatch")
        if time.time() >= ev.expires_at:
            raise EvidenceError("identity evidence expired")
        return ev
