from __future__ import annotations

import asyncio
import queue
import threading

from ..agency import UndertoneObservation
from ..media.contracts import AcousticUndertoneBackend, StreamingTTSBackend, VoiceProfile
from ..synthesis import SynthesisRequest
from ..modeling import VaakModelPlan


class AsyncMediaSynthesisBackend:
    """Bridge an async production TTS engine into the governance kernel."""
    sample_rate = 16000

    def __init__(self, backend: StreamingTTSBackend):
        self.backend = backend
        self.sample_rate = int(getattr(backend, "sample_rate", 16000))

    def stream(self, request: SynthesisRequest):
        q: queue.Queue = queue.Queue(maxsize=32)
        sentinel = object()

        async def produce():
            plan = VaakModelPlan.from_conditioning(request.conditioning)
            voice = VoiceProfile(
                voice_id=request.voice_id or request.voice_model_hash or "enrolled",
                reference_audio=request.voice_model,
                reference_text=request.reference_text,
                language=request.language,
                metadata={
                    "vaak_conditioning": request.conditioning or {},
                    "vaak_model_plan": plan.body(),
                    "vaak_model_plan_hash": plan.plan_hash(),
                    "vaak_control_tokens": list(plan.control_tokens()),
                },
            )
            try:
                async for chunk in self.backend.synthesize(
                    request.text,
                    voice=voice,
                    language=request.language,
                    prosody=request.prosody,
                    conditioning=request.conditioning,
                ):
                    if chunk.sample_rate != 16000:
                        pcm = _resample_pcm16(chunk.pcm16, chunk.sample_rate, 16000)
                    else:
                        pcm = chunk.pcm16
                    q.put(pcm)
            except BaseException as exc:
                q.put(exc)
            finally:
                q.put(sentinel)

        def runner():
            asyncio.run(produce())

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        while True:
            item = q.get()
            if item is sentinel:
                break
            if isinstance(item, BaseException):
                raise item
            yield item


class SyncUndertoneAnalyzer:
    """Bridge production async acoustic analysis into VoiceSession."""
    def __init__(self, backend: AcousticUndertoneBackend):
        self.backend = backend

    def analyze_output(self, pcm: bytes, *, prosody: dict | None) -> UndertoneObservation:
        del prosody
        obj = asyncio.run(self.backend.analyze(pcm, sample_rate=16000))
        return UndertoneObservation.from_body(obj, analyzer=str(obj.get("analyzer", "acoustic-production")))


def _resample_pcm16(pcm: bytes, src_sr: int, dst_sr: int) -> bytes:
    if src_sr == dst_sr or not pcm:
        return pcm
    import numpy as np
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    if len(x) < 2:
        return pcm
    n = max(1, int(round(len(x) * dst_sr / src_sr)))
    xp = np.linspace(0.0, 1.0, len(x), endpoint=False)
    fp = np.linspace(0.0, 1.0, n, endpoint=False)
    y = np.interp(fp, xp, x)
    return np.clip(y, -32768, 32767).astype("<i2").tobytes()
