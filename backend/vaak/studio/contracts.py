from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from enum import Enum

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ..identity import SubjectKeypair
from ..util import canon, sha256_hex


class StudioError(Exception):
    pass


class VoiceOrigin(str, Enum):
    SYNTHETIC = "synthetic"
    LICENSED_PERFORMER = "licensed_performer"
    RIGHTS_HOLDER = "rights_holder"


class UsageClass(str, Enum):
    DIALOGUE = "dialogue"
    DUBBING = "dubbing"
    INTERACTIVE = "interactive"
    PROMO = "promo"
    TRAILER = "trailer"
    LIVE = "live"
    BROADCAST = "broadcast"
    GAME = "game"
    INTERNAL = "internal"


@dataclass(frozen=True)
class StudioCharacter:
    character_id: str
    studio_id: str
    display_name: str
    persona: dict = field(default_factory=dict)
    voice_origin: VoiceOrigin = VoiceOrigin.SYNTHETIC
    performer_id: str | None = None
    canonical_voice_version: str = "1"
    performer_evidence_hash: str | None = None

    def body(self) -> dict:
        return {
            "character_id": self.character_id,
            "studio_id": self.studio_id,
            "display_name": self.display_name,
            "persona": dict(self.persona),
            "voice_origin": self.voice_origin.value,
            "performer_id": self.performer_id,
            "canonical_voice_version": self.canonical_voice_version,
            "performer_evidence_hash": self.performer_evidence_hash,
        }


@dataclass(frozen=True)
class RightsScope:
    production_ids: tuple[str, ...]
    languages: tuple[str, ...] = ("en",)
    territories: tuple[str, ...] = ("worldwide",)
    channels: tuple[str, ...] = ("local", "call", "stream")
    usage_classes: tuple[str, ...] = (UsageClass.DIALOGUE.value,)
    allow_localization: bool = True
    allow_voice_transformation: bool = False

    def body(self) -> dict:
        return {
            "production_ids": list(self.production_ids),
            "languages": list(self.languages),
            "territories": list(self.territories),
            "channels": list(self.channels),
            "usage_classes": list(self.usage_classes),
            "allow_localization": self.allow_localization,
            "allow_voice_transformation": self.allow_voice_transformation,
        }


@dataclass(frozen=True)
class StudioRightsGrant:
    grant_id: str
    rights_holder_id: str
    studio_id: str
    character_id: str
    voice_version: str
    scope: RightsScope
    issued_at: float
    expires_at: float
    rights_holder_public_key_pem: str
    rights_holder_signature: str
    performer_id: str | None = None
    performer_public_key_pem: str | None = None
    performer_signature: str | None = None
    rights_holder_evidence_hash: str | None = None
    performer_evidence_hash: str | None = None
    revoked_at: float | None = None
    revocation_reason: str | None = None

    def unsigned_body(self) -> dict:
        return {
            "type": "vaak-studio-rights-grant-v1",
            "grant_id": self.grant_id,
            "rights_holder_id": self.rights_holder_id,
            "studio_id": self.studio_id,
            "character_id": self.character_id,
            "voice_version": self.voice_version,
            "scope": self.scope.body(),
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "rights_holder_public_key_pem": self.rights_holder_public_key_pem,
            "performer_id": self.performer_id,
            "performer_public_key_pem": self.performer_public_key_pem,
            "rights_holder_evidence_hash": self.rights_holder_evidence_hash,
            "performer_evidence_hash": self.performer_evidence_hash,
        }

    def body(self) -> dict:
        return self.unsigned_body() | {
            "rights_holder_signature": self.rights_holder_signature,
            "performer_signature": self.performer_signature,
            "revoked_at": self.revoked_at,
            "revocation_reason": self.revocation_reason,
        }

    def grant_hash(self) -> str:
        return sha256_hex(canon(self.body()))

    def signature_payload(self) -> bytes:
        return canon(self.unsigned_body())

    def verify_signatures(self) -> None:
        try:
            holder: Ed25519PublicKey = serialization.load_pem_public_key(
                self.rights_holder_public_key_pem.encode()
            )
            holder.verify(bytes.fromhex(self.rights_holder_signature), self.signature_payload())
            if self.performer_id:
                if not self.performer_public_key_pem or not self.performer_signature:
                    raise StudioError("licensed performer grant missing performer consent signature")
                performer: Ed25519PublicKey = serialization.load_pem_public_key(
                    self.performer_public_key_pem.encode()
                )
                performer.verify(bytes.fromhex(self.performer_signature), self.signature_payload())
        except (InvalidSignature, ValueError, TypeError) as exc:
            raise StudioError("invalid studio rights signature") from exc

    def authorize(
        self,
        *,
        production_id: str,
        language: str,
        territory: str,
        channel: str,
        usage_class: str,
        at: float | None = None,
    ) -> None:
        self.verify_signatures()
        now = time.time() if at is None else at
        if self.revoked_at is not None and now >= self.revoked_at:
            raise StudioError("studio rights grant revoked")
        if now >= self.expires_at:
            raise StudioError("studio rights grant expired")
        if production_id not in self.scope.production_ids:
            raise StudioError("production not granted")
        if language not in self.scope.languages:
            raise StudioError("language not granted")
        if territory not in self.scope.territories and "worldwide" not in self.scope.territories:
            raise StudioError("territory not granted")
        if channel not in self.scope.channels:
            raise StudioError("channel not granted")
        if usage_class not in self.scope.usage_classes:
            raise StudioError("usage class not granted")


@dataclass(frozen=True)
class DirectorIntent:
    scene_id: str | None = None
    take_id: str | None = None
    emotion_direction: str | None = None
    pacing: str | None = None
    energy: float | None = None
    emphasis: tuple[str, ...] = ()
    accent: str | None = None
    age_presentation: str | None = None
    notes: str | None = None

    def body(self) -> dict:
        return {
            "scene_id": self.scene_id,
            "take_id": self.take_id,
            "emotion_direction": self.emotion_direction,
            "pacing": self.pacing,
            "energy": self.energy,
            "emphasis": list(self.emphasis),
            "accent": self.accent,
            "age_presentation": self.age_presentation,
            "notes": self.notes,
        }


class StudioRightsService:
    """Reference rights authority for AI Studio character voices.

    Production maps rights-holder and performer identities to authoritative
    Parinita/enterprise identity roots. This service enforces scope, expiry,
    revocation and dual-consent for licensed performer voices before a Vaak
    speech session can be opened.
    """

    def __init__(self, key_factory=None) -> None:
        self._holders: dict[str, object] = {}
        self._performers: dict[str, object] = {}
        self._grants: dict[str, StudioRightsGrant] = {}
        self._key_factory = key_factory or (lambda key_ref: SubjectKeypair())

    def rights_holder_key(self, rights_holder_id: str):
        return self._holders.setdefault(rights_holder_id, self._key_factory(f"rights-holder:{rights_holder_id}"))

    def performer_key(self, performer_id: str):
        return self._performers.setdefault(performer_id, self._key_factory(f"performer:{performer_id}"))

    def issue(
        self,
        *,
        rights_holder_id: str,
        studio_id: str,
        character: StudioCharacter,
        scope: RightsScope,
        ttl_seconds: float,
        at: float | None = None,
        rights_holder_evidence_hash: str | None = None,
        performer_evidence_hash: str | None = None,
    ) -> StudioRightsGrant:
        if character.studio_id != studio_id:
            raise StudioError("character does not belong to studio")
        issued = time.time() if at is None else at
        holder = self.rights_holder_key(rights_holder_id)
        performer = self.performer_key(character.performer_id) if character.performer_id else None
        base = StudioRightsGrant(
            grant_id=sha256_hex(canon({"studio": studio_id, "character": character.character_id, "issued": issued}))[:32],
            rights_holder_id=rights_holder_id,
            studio_id=studio_id,
            character_id=character.character_id,
            voice_version=character.canonical_voice_version,
            scope=scope,
            issued_at=issued,
            expires_at=issued + ttl_seconds,
            rights_holder_public_key_pem=holder.public_pem,
            rights_holder_signature="",
            performer_id=character.performer_id,
            performer_public_key_pem=performer.public_pem if performer else None,
            performer_signature=None,
            rights_holder_evidence_hash=rights_holder_evidence_hash,
            performer_evidence_hash=performer_evidence_hash,
        )
        payload = base.signature_payload()
        grant = replace(
            base,
            rights_holder_signature=holder.sign(payload),
            performer_signature=performer.sign(payload) if performer else None,
        )
        grant.verify_signatures()
        self._grants[grant.grant_id] = grant
        return grant

    def restore(self, grant: StudioRightsGrant) -> StudioRightsGrant:
        grant.verify_signatures()
        self._grants[grant.grant_id] = grant
        return grant

    def get(self, grant_id: str) -> StudioRightsGrant:
        try:
            return self._grants[grant_id]
        except KeyError as exc:
            raise StudioError("unknown studio rights grant") from exc

    def revoke(self, grant_id: str, reason: str, *, at: float | None = None) -> StudioRightsGrant:
        old = self.get(grant_id)
        if old.revoked_at is not None:
            return old
        updated = replace(old, revoked_at=time.time() if at is None else at, revocation_reason=str(reason))
        self._grants[grant_id] = updated
        return updated
