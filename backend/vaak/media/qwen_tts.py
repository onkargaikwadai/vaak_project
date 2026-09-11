from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass

from .contracts import AudioChunk, VoiceProfile


@dataclass
class Qwen3TTSConfig:
    model_id: str = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
    device: str = "cuda:0"
    dtype: str = "bfloat16"
    attention: str = "flash_attention_2"
    chunk_ms: int = 40


class Qwen3TTSBackend:
    """Self-hosted Qwen3-TTS voice-clone adapter.

    The official qwen-tts API currently returns a waveform per generation.
    This adapter chunks the generated waveform for Vaak's realtime transport.
    For true token-level streaming, deploy the model behind a streaming media
    gateway and use MediaGatewayTTS instead.
    """

    def __init__(self, config: Qwen3TTSConfig | None = None):
        self.config = config or Qwen3TTSConfig()
        self._model = None
        self.sample_rate = 16000
        self._prompt_cache: dict[str, object] = {}

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            import torch
            from qwen_tts import Qwen3TTSModel
        except ImportError as exc:
            raise RuntimeError("Qwen3-TTS extras are not installed; install .[qwen]") from exc
        dtype = getattr(torch, self.config.dtype)
        self._model = Qwen3TTSModel.from_pretrained(
            self.config.model_id,
            device_map=self.config.device,
            dtype=dtype,
            attn_implementation=self.config.attention,
        )
        return self._model

    def _generate(self, text: str, voice: VoiceProfile, language: str, prosody: dict | None):
        import numpy as np
        model = self._load()
        if not voice.reference_audio:
            raise RuntimeError("voice-clone synthesis requires enrolled reference audio")
        cache_key = voice.voice_id
        prompt = self._prompt_cache.get(cache_key)
        if prompt is None:
            ref = _pcm16_to_float(voice.reference_audio)
            prompt = model.create_voice_clone_prompt(
                ref_audio=(ref, 16000),
                ref_text=voice.reference_text or "",
                x_vector_only_mode=not bool(voice.reference_text),
            )
            self._prompt_cache[cache_key] = prompt
        kwargs = {}
        if prosody:
            # Qwen Base clone does not accept arbitrary structured prosody.
            # The Persona/Agency layer governs prosody; a streaming media
            # gateway may map the allowed envelope into model-specific knobs.
            kwargs.update({k: v for k, v in prosody.items() if k in {"temperature", "top_p"}})
        wavs, sr = model.generate_voice_clone(
            text=text,
            language=language,
            voice_clone_prompt=prompt,
            **kwargs,
        )
        wave = np.asarray(wavs[0], dtype=np.float32)
        pcm = _float_to_pcm16(wave)
        if int(sr) != 16000:
            pcm = _resample_pcm16(pcm, int(sr), 16000)
        self.sample_rate = 16000
        return pcm, 16000

    async def synthesize(self, text: str, *, voice: VoiceProfile, language: str = "English", prosody: dict | None = None, conditioning: dict | None = None):
        # The base Qwen clone adapter does not natively consume the complete Vaak
        # conditioning record. Dedicated Vaak fine-tunes/media gateways should.
        del conditioning
        pcm, sr = await asyncio.to_thread(self._generate, text, voice, language, prosody)
        bytes_per_chunk = max(2, int(sr * 2 * self.config.chunk_ms / 1000))
        bytes_per_chunk -= bytes_per_chunk % 2
        for i in range(0, len(pcm), bytes_per_chunk):
            yield AudioChunk(pcm[i:i + bytes_per_chunk], sr)
            await asyncio.sleep(0)


def _pcm16_to_float(pcm: bytes):
    import numpy as np
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0


def _float_to_pcm16(wave):
    import numpy as np
    x = np.clip(wave, -1.0, 1.0)
    return (x * 32767.0).astype("<i2").tobytes()


def _resample_pcm16(pcm: bytes, src_sr: int, dst_sr: int) -> bytes:
    import numpy as np
    if src_sr == dst_sr or not pcm:
        return pcm
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    n = max(1, int(round(len(x) * dst_sr / src_sr)))
    xp = np.linspace(0.0, 1.0, len(x), endpoint=False)
    fp = np.linspace(0.0, 1.0, n, endpoint=False)
    y = np.interp(fp, xp, x)
    return np.clip(y, -32768, 32767).astype("<i2").tobytes()
