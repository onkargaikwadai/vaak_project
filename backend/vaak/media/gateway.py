from __future__ import annotations

import base64
import json
from dataclasses import dataclass

from .contracts import AudioChunk, Transcript, VoiceProfile


@dataclass
class MediaGatewayConfig:
    base_url: str
    api_key: str | None = None
    timeout_s: float = 30.0


class MediaGatewayASR:
    """Adapter for Parinita's GPU media service (NeMo/Nemotron or equivalent)."""
    def __init__(self, config: MediaGatewayConfig):
        self.config = config

    async def transcribe(self, pcm16: bytes, *, sample_rate: int = 16000, language: str | None = None) -> Transcript:
        import httpx
        headers = {"Authorization": f"Bearer {self.config.api_key}"} if self.config.api_key else {}
        payload = {"audio_b64": base64.b64encode(pcm16).decode(), "sample_rate": sample_rate, "language": language}
        async with httpx.AsyncClient(timeout=self.config.timeout_s) as client:
            r = await client.post(f"{self.config.base_url.rstrip('/')}/v1/asr", json=payload, headers=headers)
            r.raise_for_status()
            obj = r.json()
        return Transcript(text=obj["text"], language=obj.get("language") or language or "en", confidence=float(obj.get("confidence", 1.0)), speaker_id=obj.get("speaker_id"))


class MediaGatewayTTS:
    """Streaming TTS adapter for a self-hosted Plane-2 media service.

    Expected endpoint: websocket /v1/tts/stream with JSON request followed by
    JSON messages {audio_b64, sample_rate} and a final {done:true}.
    """
    sample_rate = 24000

    def __init__(self, config: MediaGatewayConfig):
        self.config = config

    async def synthesize(self, text: str, *, voice: VoiceProfile, language: str = "English", prosody: dict | None = None, conditioning: dict | None = None):
        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError("websockets is required for MediaGatewayTTS") from exc
        ws_url = self.config.base_url.rstrip('/').replace('http://', 'ws://').replace('https://', 'wss://') + '/v1/tts/stream'
        headers = {"Authorization": f"Bearer {self.config.api_key}"} if self.config.api_key else None
        async with websockets.connect(ws_url, additional_headers=headers, open_timeout=self.config.timeout_s) as ws:
            await ws.send(json.dumps({
                "text": text,
                "voice_id": voice.voice_id,
                "reference_audio_b64": base64.b64encode(voice.reference_audio).decode() if voice.reference_audio else None,
                "reference_text": voice.reference_text,
                "language": language,
                "prosody": prosody or {},
                "conditioning": conditioning or {},
                "voice_metadata": voice.metadata,
            }))
            async for raw in ws:
                obj = json.loads(raw)
                if obj.get("done"):
                    break
                sr = int(obj.get("sample_rate", self.sample_rate))
                self.sample_rate = sr
                yield AudioChunk(base64.b64decode(obj["audio_b64"]), sr)


class MediaGatewayUndertone:
    def __init__(self, config: MediaGatewayConfig):
        self.config = config

    async def analyze(self, pcm16: bytes, *, sample_rate: int = 16000) -> dict:
        import httpx
        headers = {"Authorization": f"Bearer {self.config.api_key}"} if self.config.api_key else {}
        async with httpx.AsyncClient(timeout=self.config.timeout_s) as client:
            r = await client.post(
                f"{self.config.base_url.rstrip('/')}/v1/undertone",
                json={"audio_b64": base64.b64encode(pcm16).decode(), "sample_rate": sample_rate},
                headers=headers,
            )
            r.raise_for_status()
            return r.json()

class MediaGatewayVAD:
    def __init__(self, config: MediaGatewayConfig):
        self.config = config

    async def is_speech(self, pcm16: bytes, *, sample_rate: int = 16000) -> bool:
        import httpx
        headers = {"Authorization": f"Bearer {self.config.api_key}"} if self.config.api_key else {}
        async with httpx.AsyncClient(timeout=self.config.timeout_s) as client:
            r = await client.post(
                f"{self.config.base_url.rstrip('/')}/v1/vad",
                json={"audio_b64": base64.b64encode(pcm16).decode(), "sample_rate": sample_rate},
                headers=headers,
            )
            r.raise_for_status()
            return bool(r.json().get("speech", False))
