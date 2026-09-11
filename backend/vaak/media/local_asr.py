from __future__ import annotations

import asyncio
import tempfile
import wave
from dataclasses import dataclass

from .contracts import Transcript


@dataclass
class FasterWhisperConfig:
    model: str = "large-v3-turbo"
    device: str = "cuda"
    compute_type: str = "float16"
    beam_size: int = 1


class FasterWhisperASR:
    """Self-hosted ASR adapter for deployments that do not use the media gateway."""
    def __init__(self, config: FasterWhisperConfig | None = None):
        self.config = config or FasterWhisperConfig()
        self._model = None

    def _load(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise RuntimeError("faster-whisper is not installed; install .[local-asr]") from exc
            self._model = WhisperModel(self.config.model, device=self.config.device, compute_type=self.config.compute_type)
        return self._model

    def _transcribe(self, pcm16: bytes, sample_rate: int, language: str | None):
        model = self._load()
        with tempfile.NamedTemporaryFile(suffix=".wav") as f:
            with wave.open(f.name, "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(sample_rate); w.writeframes(pcm16)
            segments, info = model.transcribe(f.name, language=language, beam_size=self.config.beam_size, vad_filter=True)
            text = " ".join(seg.text.strip() for seg in segments).strip()
            return Transcript(text=text, language=getattr(info, "language", None) or language or "en", confidence=1.0)

    async def transcribe(self, pcm16: bytes, *, sample_rate: int = 16000, language: str | None = None) -> Transcript:
        return await asyncio.to_thread(self._transcribe, pcm16, sample_rate, language)
