from __future__ import annotations

from dataclasses import dataclass, field

from ..voice_model import VoiceIdentityClass


@dataclass(frozen=True)
class PrincipalProfile:
    """Vaak-side identity metadata for a voice principal.

    This is deliberately not a Digital Twin runtime. Cognition, memory, goals,
    tool use, and Self Graph state remain the responsibility of the external
    host (for example Atma) or a third-party developer agent.
    """

    principal_id: str
    controller_id: str
    display_name: str
    identity_class: VoiceIdentityClass = VoiceIdentityClass.DEVELOPER_AGENT
    persona: dict = field(default_factory=dict)
    tenant_id: str = "default"

    def body(self) -> dict:
        return {
            "principal_id": self.principal_id,
            "controller_id": self.controller_id,
            "display_name": self.display_name,
            "tenant_id": self.tenant_id,
            "identity_class": self.identity_class.value,
            "persona": dict(self.persona),
        }
