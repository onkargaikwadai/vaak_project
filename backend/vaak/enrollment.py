"""vaak.enrollment — consent-gated, liveness-challenged voice enrollment.

A voice model may only be created inside an enrollment session: the daemon
issues a fresh per-session challenge passage and requires the submission to
match that challenge. This rejects a stale replay of a prior passage, but the
reference module does not itself perform biometric liveness or speaker
verification; those are production media/enrollment responsibilities. The
consent artifact is the record submitted to the reference anchoring path.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

from .identity import SubjectKeypair
from .util import canon, sha256_hex
from .vault import VoiceVault, StoredAsset

_CHALLENGE_WORDS = (
    "amber", "corridor", "granite", "monsoon", "vellum", "quartz",
    "saffron", "meridian", "cobalt", "juniper", "basalt", "harbor",
)


class EnrollmentError(Exception):
    pass


@dataclass
class EnrollmentSession:
    session_id: str
    twin_id: str
    challenge: str
    created_at: float
    ttl_seconds: float = 600.0

    def expired(self, at: float | None = None) -> bool:
        now = time.time() if at is None else at
        return now >= self.created_at + self.ttl_seconds


@dataclass(frozen=True)
class ConsentRecord:
    """Voice birth record submitted to the anchor path; production target is Chrysalis."""

    twin_id: str
    asset_id: str
    voice_model_hash: str
    challenge: str
    enrolled_at: float
    owner_signature: str
    enabled_languages: tuple[str, ...] = ("en",)  # multilingual is opt-in per language
    enrollment_evidence_hash: str | None = None

    def body(self) -> dict:
        return {
            "twin_id": self.twin_id,
            "asset_id": self.asset_id,
            "voice_model_hash": self.voice_model_hash,
            "challenge": self.challenge,
            "enrolled_at": self.enrolled_at,
            "enabled_languages": list(self.enabled_languages),
            "enrollment_evidence_hash": self.enrollment_evidence_hash,
        }

    def record_hash(self) -> str:
        return sha256_hex(canon(self.body()))


class EnrollmentService:
    def __init__(self, vault: VoiceVault):
        self._vault = vault
        self._sessions: dict[str, EnrollmentSession] = {}
        self._consents: dict[str, ConsentRecord] = {}  # by asset_id

    def open_session(self, twin_id: str, at: float | None = None) -> EnrollmentSession:
        rng = secrets.SystemRandom()
        challenge = " ".join(rng.choice(_CHALLENGE_WORDS) for _ in range(6))
        session = EnrollmentSession(
            session_id=secrets.token_hex(16),
            twin_id=twin_id,
            challenge=challenge,
            created_at=time.time() if at is None else at,
        )
        self._sessions[session.session_id] = session
        return session


    def challenge_for(self, session_id: str) -> str:
        session = self._sessions.get(session_id)
        if session is None:
            raise EnrollmentError("no such enrollment session")
        if session.expired():
            raise EnrollmentError("enrollment session expired")
        return session.challenge

    def complete(
        self,
        session_id: str,
        model_bytes: bytes,
        spoken_challenge: str,
        owner: SubjectKeypair,
        languages: tuple[str, ...] = ("en",),
        at: float | None = None,
        enrollment_evidence_hash: str | None = None,
    ) -> tuple[StoredAsset, ConsentRecord]:
        session = self._sessions.pop(session_id, None)
        if session is None:
            raise EnrollmentError("no such enrollment session")
        if session.expired(at):
            raise EnrollmentError("enrollment session expired")
        if spoken_challenge.strip() != session.challenge:
            raise EnrollmentError("liveness challenge mismatch")
        asset = self._vault.store(session.twin_id, model_bytes)
        enrolled_at = time.time() if at is None else at
        body = {
            "twin_id": session.twin_id,
            "asset_id": asset.asset_id,
            "voice_model_hash": asset.voice_model_hash,
            "challenge": session.challenge,
            "enrolled_at": enrolled_at,
            "enabled_languages": list(languages),
            "enrollment_evidence_hash": enrollment_evidence_hash,
        }
        consent = ConsentRecord(
            twin_id=session.twin_id,
            asset_id=asset.asset_id,
            voice_model_hash=asset.voice_model_hash,
            challenge=session.challenge,
            enrolled_at=enrolled_at,
            owner_signature=owner.sign(canon(body)),
            enabled_languages=languages,
            enrollment_evidence_hash=enrollment_evidence_hash,
        )
        self._consents[asset.asset_id] = consent
        return asset, consent

    def consent_for(self, asset_id: str) -> ConsentRecord:
        consent = self._consents.get(asset_id)
        if consent is None:
            raise EnrollmentError("no consent record for asset")
        return consent

    def enable_language(self, asset_id: str, language: str, owner: SubjectKeypair) -> ConsentRecord:
        """Multilingual is a flagged capability: explicit per-language enablement."""
        old = self.consent_for(asset_id)
        langs = tuple(dict.fromkeys((*old.enabled_languages, language)))
        body = dict(old.body(), enabled_languages=list(langs))
        updated = ConsentRecord(
            twin_id=old.twin_id,
            asset_id=old.asset_id,
            voice_model_hash=old.voice_model_hash,
            challenge=old.challenge,
            enrolled_at=old.enrolled_at,
            owner_signature=owner.sign(canon(body)),
            enabled_languages=langs,
            enrollment_evidence_hash=old.enrollment_evidence_hash,
        )
        self._consents[asset_id] = updated
        return updated
