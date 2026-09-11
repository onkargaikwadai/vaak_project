from __future__ import annotations

import io
from dataclasses import dataclass

from .contracts import AudioChunk, VoiceProfile


@dataclass
class KokoroTTSConfig:
    api_key: str
    base_url: str = "https://bluroute.denizenblu.com"
    model: str = "kokoro-82m"
    voice: str = "af_bella"
    chunk_ms: int = 40
    timeout_s: float = 60.0


class KokoroTTSBackend:
    """Development/test adapter for the Bluro ute Kokoro speech endpoint."""

    def __init__(self, config: KokoroTTSConfig):
        self.config = config
        self.sample_rate = 24000

    async def synthesize(
        self,
        text: str,
        *,
        voice: VoiceProfile,
        language: str = "English",
        prosody: dict | None = None,
        conditioning: dict | None = None,
    ):
        del voice, language, prosody, conditioning
        import httpx
        import wave

        payload = {
            "model": self.config.model,
            "input": text,
            "voice": self.config.voice,
            "response_format": "wav",
        }
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self.config.timeout_s) as client:
            response = await client.post(
                f"{self.config.base_url.rstrip('/')}/v1/audio/speech",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            wav_bytes = response.content

        with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            sample_rate = wav.getframerate()
            frames = wav.readframes(wav.getnframes())

        if sample_width != 2:
            raise RuntimeError(f"Kokoro WAV must be 16-bit PCM, got sample width {sample_width}")
        if channels != 1:
            frames = _mono_mixdown(frames, channels)

        self.sample_rate = int(sample_rate)
        bytes_per_chunk = max(2, int(self.sample_rate * 2 * self.config.chunk_ms / 1000))
        bytes_per_chunk -= bytes_per_chunk % 2
        for i in range(0, len(frames), bytes_per_chunk):
            yield AudioChunk(frames[i:i + bytes_per_chunk], self.sample_rate)


def _mono_mixdown(pcm16: bytes, channels: int) -> bytes:
    import struct

    samples = struct.unpack(f"<{len(pcm16) // 2}h", pcm16)
    mixed = [
        int(sum(samples[i:i + channels]) / channels)
        for i in range(0, len(samples), channels)
    ]
    return struct.pack(f"<{len(mixed)}h", *mixed)
