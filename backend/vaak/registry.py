"""vaak.registry — voice lifecycle status for reference verification.

Given a claimed voice-model identity and a valid reference watermark, the
registry distinguishes current authorization from a voice asset that has been
erased or administratively revoked. The reference detection key is derived
separately from the generative asset, so erasing the model does not by itself
remove the ability to check previously emitted reference audio.

This module does not claim universal deepfake detection and does not infer
provenance for audio outside the Vaak proof path.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from .util import canon, sha256_hex


class VoiceStatus(str, Enum):
    AUTHORIZED = "authorized"
    FORMERLY_AUTHORIZED = "formerly_authorized"
    NOT_OURS = "not_ours"
    UNKNOWN_BEACON = "unknown_beacon"


@dataclass(frozen=True)
class DeathCertificate:
    """Voice-erasure record submitted to the anchor path; production target is Chrysalis."""

    twin_id: str
    asset_id: str
    voice_model_hash: str
    erased_at: float
    owner_signature: str
    revoked_credential_ids: tuple[str, ...] = ()
    terminated_session_ids: tuple[str, ...] = ()

    def body(self) -> dict:
        return {
            "twin_id": self.twin_id,
            "asset_id": self.asset_id,
            "voice_model_hash": self.voice_model_hash,
            "erased_at": self.erased_at,
            "revoked_credential_ids": list(self.revoked_credential_ids),
            "terminated_session_ids": list(self.terminated_session_ids),
        }

    def certificate_hash(self) -> str:
        return sha256_hex(canon(self.body()))


@dataclass
class VoiceRecord:
    twin_id: str
    asset_id: str
    voice_model_hash: str
    enrolled_at: float
    erased_at: float | None = None
    revoked_at: float | None = None
    death_certificate: DeathCertificate | None = None


class StatusRegistry:
    def __init__(self) -> None:
        self._by_hash: dict[str, VoiceRecord] = {}

    def register(self, twin_id: str, asset_id: str, voice_model_hash: str,
                 enrolled_at: float | None = None) -> VoiceRecord:
        rec = VoiceRecord(
            twin_id=twin_id, asset_id=asset_id,
            voice_model_hash=voice_model_hash,
            enrolled_at=time.time() if enrolled_at is None else enrolled_at,
        )
        self._by_hash[voice_model_hash] = rec
        return rec

    def mark_erased(self, voice_model_hash: str,
                    certificate: DeathCertificate) -> None:
        rec = self._by_hash[voice_model_hash]
        rec.erased_at = certificate.erased_at
        rec.death_certificate = certificate

    def mark_revoked(self, voice_model_hash: str,
                     at: float | None = None) -> None:
        rec = self._by_hash[voice_model_hash]
        rec.revoked_at = time.time() if at is None else at

    def status(self, voice_model_hash: str,
               watermark_present: bool) -> dict:
        rec = self._by_hash.get(voice_model_hash)
        if rec is None or not watermark_present:
            return {"status": VoiceStatus.NOT_OURS.value}
        if rec.erased_at is not None:
            return {
                "status": VoiceStatus.FORMERLY_AUTHORIZED.value,
                "erased_at": rec.erased_at,
                "death_certificate_hash":
                    rec.death_certificate.certificate_hash()
                    if rec.death_certificate else None,
            }
        if rec.revoked_at is not None:
            return {
                "status": VoiceStatus.FORMERLY_AUTHORIZED.value,
                "revoked_at": rec.revoked_at,
            }
        return {"status": VoiceStatus.AUTHORIZED.value}
