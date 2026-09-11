from __future__ import annotations

import asyncio
import math
import struct

from .contracts import ASRBackend, AcousticUndertoneBackend, AudioChunk, Transcript, VoiceProfile


class DeterministicASR:
    """CI-only ASR. The transcript is supplied as UTF-8 bytes in tests."""
    async def transcribe(self, pcm16: bytes, *, sample_rate: int = 16000, language: str | None = None) -> Transcript:
        del sample_rate
        try:
            text = pcm16.decode("utf-8")
        except UnicodeDecodeError:
            text = ""
        return Transcript(text=text, language=language or "en")


class DeterministicTTS:
    """CI-only speech backend. Never selected by production config."""
    sample_rate = 16000

    async def synthesize(self, text: str, *, voice: VoiceProfile, language: str = "English", prosody: dict | None = None, conditioning: dict | None = None):
        del language, prosody, conditioning
        freq = 180 + (sum(voice.voice_id.encode()) % 160)
        phase = 0.0
        step = 2 * math.pi * freq / self.sample_rate
        words = text.split() or [""]
        for _ in words:
            vals = []
            for _ in range(640):
                vals.append(int(9000 * math.sin(phase)))
                phase += step
            await asyncio.sleep(0)
            yield AudioChunk(struct.pack("<640h", *vals), self.sample_rate)


class DeterministicUndertone:
    async def analyze(self, pcm16: bytes, *, sample_rate: int = 16000) -> dict:
        del pcm16, sample_rate
        return {"urgency": 0.0, "dominance": 0.0, "pressure_escalation": 0.0, "confidence": 1.0, "analyzer": "deterministic-ci"}

class EnergyVAD:
    """Small CI/local VAD. Production should use the media gateway or Silero-class VAD."""
    def __init__(self, threshold: float = 350.0):
        self.threshold = threshold

    async def is_speech(self, pcm16: bytes, *, sample_rate: int = 16000) -> bool:
        del sample_rate
        if len(pcm16) < 2:
            return False
        import numpy as np
        x = np.frombuffer(pcm16, dtype="<i2").astype(np.float32)
        return bool(np.sqrt(np.mean(x * x)) >= self.threshold)
