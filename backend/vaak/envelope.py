"""vaak.envelope — voice verbs under the Delegation Envelope.

The envelope is owner-signed policy. Vaak enforces the VOICE subset:
which utterance classes the twin may produce, on which channels, and the
telephony posture. Deny-by-default: an absent verb is a refused verb.
In production, enforcement replicates to Crucible at BF-3 (sovereign
egress); this module is the daemon-side authoritative check.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from .identity import SubjectKeypair
from .agency import AgencyPolicy
from .util import canon, sha256_hex


class EnvelopeError(Exception):
    pass


class VoiceVerb(str, Enum):
    SPEAK_PRIVATE = "speak_private"      # owner-facing conversation
    SPEAK_PUBLIC = "speak_public"        # audiences beyond the owner
    TELEPHONY_LISTEN = "telephony_listen"    # join calls, transcribe only
    TELEPHONY_SPEAK = "telephony_speak"      # bounded speaking on calls
    BROADCAST = "broadcast"              # Tier 2 staged/streamed presence


class Register(str, Enum):
    """Contextual voice registers — the same voice in different rooms.
    Registers are envelope-governed like verbs: an ungranted register is a
    refused register, and the register in force is recorded per manifest."""
    PRIVATE = "private"      # owner/family register
    PROFESSIONAL = "professional"  # team/practice register
    BOARD = "board"          # formal high-stakes register
    STAGE = "stage"          # broadcast/performance register


class ChannelClass(str, Enum):
    LOCAL = "local"          # owner session at the POP / Presence device
    CALL = "call"            # SIP/WebRTC leg via Corridor
    STREAM = "stream"        # broadcast delivery (6B/6C/6D path)


# Which verbs are valid on which channel — structure, not policy.
_CHANNEL_VERBS = {
    ChannelClass.LOCAL: {VoiceVerb.SPEAK_PRIVATE, VoiceVerb.SPEAK_PUBLIC},
    ChannelClass.CALL: {VoiceVerb.TELEPHONY_LISTEN, VoiceVerb.TELEPHONY_SPEAK},
    ChannelClass.STREAM: {VoiceVerb.BROADCAST},
}


@dataclass(frozen=True)
class VoiceEnvelope:
    """Owner-signed voice policy for one twin. Versioned; no unsigned edits."""

    twin_id: str
    version: int
    granted: frozenset[VoiceVerb]
    high_consequence: frozenset[VoiceVerb]  # require presence check per session
    registers: frozenset[Register]  # granted prosody registers
    disclose_on_calls: bool  # twin self-identifies at call open
    agency_policy: AgencyPolicy  # non-manipulation / prosody policy
    owner_signature: str

    def body(self) -> dict:
        return {
            "twin_id": self.twin_id,
            "version": self.version,
            "granted": sorted(v.value for v in self.granted),
            "high_consequence": sorted(v.value for v in self.high_consequence),
            "registers": sorted(r.value for r in self.registers),
            "disclose_on_calls": self.disclose_on_calls,
            "agency_policy": self.agency_policy.body(),
        }

    def envelope_hash(self) -> str:
        return sha256_hex(canon(self.body()))


def sign_envelope(
    twin_id: str,
    version: int,
    granted: set[VoiceVerb],
    owner: SubjectKeypair,
    high_consequence: set[VoiceVerb] | None = None,
    registers: set[Register] | None = None,
    disclose_on_calls: bool = True,
    agency_policy: AgencyPolicy | None = None,
) -> VoiceEnvelope:
    hc = frozenset(high_consequence or set())
    regs = frozenset(registers if registers is not None else {Register.PRIVATE})
    policy = agency_policy or AgencyPolicy()
    body = {
        "twin_id": twin_id,
        "version": version,
        "granted": sorted(v.value for v in granted),
        "high_consequence": sorted(v.value for v in hc),
        "registers": sorted(r.value for r in regs),
        "disclose_on_calls": disclose_on_calls,
        "agency_policy": policy.body(),
    }
    return VoiceEnvelope(
        twin_id=twin_id,
        version=version,
        granted=frozenset(granted),
        high_consequence=hc,
        registers=regs,
        disclose_on_calls=disclose_on_calls,
        agency_policy=policy,
        owner_signature=owner.sign(canon(body)),
    )


@dataclass
class Authorization:
    verb: VoiceVerb
    channel: ChannelClass
    register: Register
    envelope_version: int
    envelope_hash: str
    presence_checked: bool
    envelope_body: dict
    envelope_owner_signature: str
    agency_policy: AgencyPolicy
    granted_at: float = field(default_factory=time.time)


class EnvelopeEnforcer:
    def __init__(self, envelope: VoiceEnvelope):
        self.envelope = envelope

    def authorize(
        self,
        verb: VoiceVerb,
        channel: ChannelClass,
        register: Register = Register.PRIVATE,
        presence_checked: bool = False,
    ) -> Authorization:
        if verb not in _CHANNEL_VERBS[channel]:
            raise EnvelopeError(f"{verb.value} is not valid on channel {channel.value}")
        if verb not in self.envelope.granted:
            raise EnvelopeError(f"{verb.value} not granted by envelope (deny-by-default)")
        if verb in self.envelope.high_consequence and not presence_checked:
            raise EnvelopeError(f"{verb.value} requires owner presence check")
        if register not in self.envelope.registers:
            raise EnvelopeError(f"register '{register.value}' not granted by envelope")
        return Authorization(
            verb=verb,
            channel=channel,
            register=register,
            envelope_version=self.envelope.version,
            envelope_hash=self.envelope.envelope_hash(),
            presence_checked=presence_checked,
            envelope_body=self.envelope.body(),
            envelope_owner_signature=self.envelope.owner_signature,
            agency_policy=self.envelope.agency_policy,
        )
