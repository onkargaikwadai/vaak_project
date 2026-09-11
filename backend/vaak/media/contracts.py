from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str = "en"
    is_final: bool = True
    confidence: float = 1.0
    speaker_id: str | None = None


@dataclass(frozen=True)
class AudioChunk:
    pcm16: bytes
    sample_rate: int = 16000
    channels: int = 1


@dataclass(frozen=True)
class VoiceProfile:
    voice_id: str
    reference_audio: bytes | None = None
    reference_text: str | None = None
    language: str = "English"
    metadata: dict = field(default_factory=dict)


class ASRBackend(Protocol):
    async def transcribe(self, pcm16: bytes, *, sample_rate: int = 16000, language: str | None = None) -> Transcript: ...


class StreamingTTSBackend(Protocol):
    sample_rate: int

    async def synthesize(
        self,
        text: str,
        *,
        voice: VoiceProfile,
        language: str = "English",
        prosody: dict | None = None,
        conditioning: dict | None = None,
    ) -> AsyncIterator[AudioChunk]: ...


class AcousticUndertoneBackend(Protocol):
    async def analyze(self, pcm16: bytes, *, sample_rate: int = 16000) -> dict: ...


class VADBackend(Protocol):
    async def is_speech(self, pcm16: bytes, *, sample_rate: int = 16000) -> bool: ...
