from __future__ import annotations

import asyncio
import base64
import contextlib
from dataclasses import dataclass, field

from .runtime import ProductRuntime


@dataclass
class RealtimeConversation:
    """Realtime Vaak media session with an external cognition boundary.

    Input audio becomes transcript/undertone events. Vaak never generates the
    cognitive response itself. Atma or any developer agent sends response.create
    with candidate text, which is then governed and voiced by Vaak.
    """

    runtime: ProductRuntime
    session_id: str
    mode: str = "external_agent"
    language: str = "en"
    audio_buffer: bytearray = field(default_factory=bytearray)
    response_task: asyncio.Task | None = None
    max_buffer_bytes: int = 8 * 1024 * 1024
    max_response_chars: int = 20000

    async def handle(self, event: dict, send) -> None:
        etype = event.get("type")
        if etype == "input_audio_buffer.append":
            audio = base64.b64decode(event.get("audio", ""))
            if audio and self.response_task and not self.response_task.done() and await self.runtime.is_speech(audio):
                self.runtime.interrupt(self.session_id)
                await send({"type": "response.interrupted"})
            if len(self.audio_buffer) + len(audio) > self.max_buffer_bytes:
                self.audio_buffer.clear()
                raise ValueError("realtime audio buffer limit exceeded")
            self.audio_buffer.extend(audio)
            return

        if etype == "input_audio_buffer.clear":
            self.audio_buffer.clear()
            return

        if etype == "input_audio_buffer.commit":
            pcm = bytes(self.audio_buffer)
            self.audio_buffer.clear()
            text, undertone = await self.runtime.transcribe(pcm, language=self.language)
            report = self.runtime.observe_undertone(self.session_id, undertone)
            await send({"type": "input_audio.transcript", "text": text})
            await send({"type": "input_audio.undertone", "observation": undertone, "agency_report": report})
            await send({"type": "response.requested", "input_text": text})
            return

        if etype == "response.create":
            text = str(event.get("text") or "")
            if not text:
                raise ValueError("response.create requires externally generated text")
            if len(text) > self.max_response_chars:
                raise ValueError("response.create text exceeds limit")
            await self._start_response(text, send, prosody=event.get("prosody"), agency=event.get("agency"))
            return

        if etype == "recipient.refusal":
            report = self.runtime.refusal(self.session_id)
            await send({"type": "agency.refusal_recorded", "agency_report": report})
            return

        if etype == "attestation.challenge":
            await send({"type": "attestation.response", "attestation": self.runtime.attest(self.session_id, str(event.get("nonce") or ""))})
            return

        if etype == "session.close":
            if self.response_task and not self.response_task.done():
                self.runtime.interrupt(self.session_id)
                self.response_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.response_task
            manifest = self.runtime.close_live(self.session_id)
            await send({"type": "session.closed", "manifest": manifest})
            return

        raise ValueError(f"unsupported realtime event: {etype}")

    async def _start_response(self, text: str, send, *, prosody: dict | None = None, agency: dict | None = None):
        if self.response_task and not self.response_task.done():
            self.runtime.interrupt(self.session_id)
            self.response_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.response_task

        async def run():
            try:
                await send({"type": "response.text", "text": text})
                async for pcm in self.runtime.speak_stream(self.session_id, text, prosody=prosody, agency=agency):
                    await send({"type": "response.audio.delta", "audio": base64.b64encode(pcm).decode(), "sample_rate": 16000})
                await send({"type": "response.done"})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await send({"type": "response.error", "error": type(exc).__name__, "message": str(exc)})

        self.response_task = asyncio.create_task(run())
