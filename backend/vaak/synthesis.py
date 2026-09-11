"""vaak.synthesis — pluggable synthesis backend contract.

The media team's open-weight P2 backend implements SynthesisBackend; the
governance side of the daemon is backend-agnostic. StubBackend exists so
the contract, envelope, watermark, and provenance layers are testable
before the real pipeline lands. The bake-off = swapping StubBackend for
candidate open-weight backends behind the same interface.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Iterator, Protocol

SAMPLE_RATE = 16_000
CHUNK_SAMPLES = 160  # 10ms chunks — matches watermark frame size


@dataclass
class SynthesisRequest:
    text: str
    voice_model: bytes
    language: str = "en"
    # Prosody conditioning from the Persona (pace/warmth/register) — opaque here.
    prosody: dict | None = None
    voice_id: str | None = None
    voice_model_hash: str | None = None
    reference_text: str | None = None
    # Vaak-native identity/persona/authority/studio conditioning contract.
    conditioning: dict | None = None


class SynthesisBackend(Protocol):
    def stream(self, request: SynthesisRequest) -> Iterator[bytes]:
        """Yield 16-bit mono PCM chunks at SAMPLE_RATE. Must be incremental."""
        ...


class StubBackend:
    """Deterministic tone generator: N chunks per word. Contract-testing only."""

    def stream(self, request: SynthesisRequest) -> Iterator[bytes]:
        words = request.text.split() or [""]
        freq = 220.0 + (sum(request.voice_model[:8]) % 200)  # voice-dependent pitch
        phase = 0.0
        step = 2 * math.pi * freq / SAMPLE_RATE
        for _word in words:
            for _chunk in range(4):  # 40ms per word
                samples = []
                for _ in range(CHUNK_SAMPLES):
                    samples.append(int(12_000 * math.sin(phase)))
                    phase += step
                yield struct.pack(f"<{CHUNK_SAMPLES}h", *samples)
