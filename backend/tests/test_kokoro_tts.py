import asyncio
import io
import wave

from vaak.media.contracts import VoiceProfile
from vaak.media.kokoro_tts import KokoroTTSBackend, KokoroTTSConfig


def _wav_bytes() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\x01\x00" * 2400)
    return buf.getvalue()


class _FakeResponse:
    content = _wav_bytes()

    def raise_for_status(self):
        return None


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, *, json, headers):
        assert url == "https://bluroute.denizenblu.com/v1/audio/speech"
        assert headers["Authorization"] == "Bearer test-key"
        assert json["model"] == "kokoro-82m"
        assert json["voice"] == "af_bella"
        assert json["response_format"] == "wav"
        return _FakeResponse()


def test_kokoro_tts_backend_streams_bluro_wav(monkeypatch):
    monkeypatch.setattr("httpx.AsyncClient", _FakeAsyncClient)
    backend = KokoroTTSBackend(KokoroTTSConfig(api_key="test-key"))

    async def run():
        return [
            chunk
            async for chunk in backend.synthesize(
                "Hello from Vaak",
                voice=VoiceProfile("test-voice"),
            )
        ]

    chunks = asyncio.run(run())

    assert chunks
    assert chunks[0].sample_rate == 24000
    assert sum(len(chunk.pcm16) for chunk in chunks) == 4800
