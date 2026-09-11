"""Parinita Vaak Voice Model (VVM) native conditioning and training contract.

This module is deliberately backbone-neutral. It defines the model-facing plan
that a Parinita VVM checkpoint/gateway consumes and the multi-objective training
contract that distinguishes VVM from commodity text + speaker-embedding TTS.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..util import canon, sha256_hex


@dataclass(frozen=True)
class VaakTrainingObjectives:
    identity_retention: float = 1.0
    semantic_fidelity: float = 1.0
    persona_register_consistency: float = 0.8
    authority_adherence: float = 1.0
    influence_policy_adherence: float = 1.2
    undertone_response_safety: float = 1.2
    conversational_continuity: float = 0.7
    studio_direction_fidelity: float = 0.8
    rights_scope_adherence: float = 1.2
    provenance_alignment: float = 0.5

    def body(self) -> dict:
        return self.__dict__.copy()


@dataclass(frozen=True)
class VaakModelPlan:
    principal_id: str
    identity_class: str
    identity: dict = field(default_factory=dict)
    persona: dict = field(default_factory=dict)
    register: str = "private"
    relationship: dict = field(default_factory=dict)
    authority: dict = field(default_factory=dict)
    influence: dict = field(default_factory=dict)
    undertone: dict = field(default_factory=dict)
    channel: str = "local"
    locale: str = "en"
    director: dict = field(default_factory=dict)
    production: dict = field(default_factory=dict)
    performance_version: str | None = None
    rights_grant_hash: str | None = None
    objectives: VaakTrainingObjectives = VaakTrainingObjectives()

    @classmethod
    def from_conditioning(cls, conditioning: dict | None) -> "VaakModelPlan":
        c = dict(conditioning or {})
        principal_id = str(c.get("principal_id") or "unknown")
        identity_class = str(c.get("identity_class") or "developer_agent")
        return cls(
            principal_id=principal_id,
            identity_class=identity_class,
            identity={"principal_id": principal_id, "identity_class": identity_class},
            persona=dict(c.get("persona") or {}),
            register=str(c.get("register") or "private"),
            relationship=dict(c.get("relationship") or {}),
            authority=dict(c.get("authority") or {}),
            influence=dict(c.get("influence") or {}),
            undertone=dict(c.get("incoming_undertone") or {}),
            channel=str(c.get("channel") or "local"),
            locale=str(c.get("locale") or "en"),
            director=dict(c.get("director") or {}),
            production=dict(c.get("production") or {}),
            performance_version=c.get("performance_version"),
            rights_grant_hash=c.get("rights_grant_hash"),
        )

    def body(self) -> dict:
        return {
            "type": "parinita-vaak-voice-model-plan-v1",
            "principal_id": self.principal_id,
            "identity_class": self.identity_class,
            "identity": self.identity,
            "persona": self.persona,
            "register": self.register,
            "relationship": self.relationship,
            "authority": self.authority,
            "influence": self.influence,
            "undertone": self.undertone,
            "channel": self.channel,
            "locale": self.locale,
            "director": self.director,
            "production": self.production,
            "performance_version": self.performance_version,
            "rights_grant_hash": self.rights_grant_hash,
            "objectives": self.objectives.body(),
        }

    def plan_hash(self) -> str:
        return sha256_hex(canon(self.body()))

    def control_tokens(self) -> tuple[str, ...]:
        """Backbone-neutral symbolic controls for adapters/tokenizers.

        Production VVM checkpoints may replace these tokens with learned
        embeddings; keeping a canonical symbolic form makes training data and
        inference traces reproducible and auditable.
        """
        tokens = [
            f"<vaak:class={self.identity_class}>",
            f"<vaak:principal={self.principal_id}>",
            f"<vaak:register={self.register}>",
            f"<vaak:channel={self.channel}>",
            f"<vaak:locale={self.locale}>",
        ]
        if self.performance_version:
            tokens.append(f"<vaak:performance={self.performance_version}>")
        if self.rights_grant_hash:
            tokens.append(f"<vaak:rights={self.rights_grant_hash[:16]}>")
        if self.production.get("production_id"):
            tokens.append(f"<vaak:production={self.production['production_id']}>")
        return tuple(tokens)
