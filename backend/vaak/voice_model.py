"""Vaak Voice Model conditioning contracts.

Vaak is not modeled as text + speaker embedding. The conditioning record binds
speech generation to a persistent identity, persona/register, relationship,
authority/influence posture, undertone observations, channel context and (for
studio characters) production/rightsholder direction. Media backends may map
these fields to native controls, adapters or fine-tuned encoders, but they may
not silently widen the signed governance envelope.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .util import canon, sha256_hex


class VoiceIdentityClass(str, Enum):
    ATMA_EXTERNAL = "atma_external"
    STUDIO_CHARACTER = "studio_character"
    DEVELOPER_AGENT = "developer_agent"
    BRAND_CHARACTER = "brand_character"


@dataclass(frozen=True)
class VoiceConditioning:
    principal_id: str
    identity_class: VoiceIdentityClass = VoiceIdentityClass.DEVELOPER_AGENT
    persona: dict = field(default_factory=dict)
    register: str = "private"
    relationship: dict = field(default_factory=dict)
    authority: dict = field(default_factory=dict)
    influence: dict = field(default_factory=dict)
    incoming_undertone: dict = field(default_factory=dict)
    channel: str = "local"
    locale: str = "en"
    director: dict = field(default_factory=dict)
    production: dict = field(default_factory=dict)
    performance_version: str | None = None
    rights_grant_hash: str | None = None

    def body(self) -> dict:
        return {
            "principal_id": self.principal_id,
            "identity_class": self.identity_class.value,
            "persona": dict(self.persona),
            "register": self.register,
            "relationship": dict(self.relationship),
            "authority": dict(self.authority),
            "influence": dict(self.influence),
            "incoming_undertone": dict(self.incoming_undertone),
            "channel": self.channel,
            "locale": self.locale,
            "director": dict(self.director),
            "production": dict(self.production),
            "performance_version": self.performance_version,
            "rights_grant_hash": self.rights_grant_hash,
        }

    def conditioning_hash(self) -> str:
        return sha256_hex(canon(self.body()))


@dataclass(frozen=True)
class VaakVoiceModelSpec:
    """The stable contract the media model is expected to consume.

    VVM keeps this contract stable even when different acoustic backbones encode fields differently. The gateway
    receives this record so Vaak-specific encoders/fine-tunes can progressively
    move governance-aware control into the model without changing the public
    product/session contract.
    """

    family: str = "Parinita Vaak Voice Model"
    contract_version: str = "2.0"
    supports_identity_conditioning: bool = True
    supports_persona_conditioning: bool = True
    supports_authority_conditioning: bool = True
    supports_influence_conditioning: bool = True
    supports_undertone_conditioning: bool = True
    supports_studio_direction: bool = True
