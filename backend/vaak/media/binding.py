from __future__ import annotations

import base64
from dataclasses import dataclass


@dataclass(frozen=True)
class BoundAudio:
    pcm16: bytes
    binding_ref: str
    scheme: str = "vaak-soft-binding-v1"


@dataclass(frozen=True)
class SoftBindingConfig:
    base_url: str
    api_key: str | None = None
    timeout_s: float = 8.0


class HTTPSoftBindingBackend:
    """Production soft-binding/watermark service boundary.

    The service must return the marked PCM that is safe to emit plus a durable
    binding reference. Failure is propagated so the authorized egress path
    fails closed rather than emitting unbound media.
    """

    def __init__(self, config: SoftBindingConfig):
        self.config = config

    def bind(self, pcm16: bytes, *, sample_rate: int, context: dict) -> BoundAudio:
        import httpx
        headers = {"Authorization": f"Bearer {self.config.api_key}"} if self.config.api_key else {}
        with httpx.Client(timeout=self.config.timeout_s) as client:
            r = client.post(
                f"{self.config.base_url.rstrip('/')}/v1/audio/bind",
                json={
                    "audio_b64": base64.b64encode(pcm16).decode(),
                    "sample_rate": sample_rate,
                    "context": context,
                },
                headers=headers,
            )
            r.raise_for_status()
            obj = r.json()
        marked = base64.b64decode(obj["audio_b64"])
        if not marked:
            raise RuntimeError("soft-binding service returned empty audio")
        ref = str(obj.get("binding_ref") or "")
        if not ref:
            raise RuntimeError("soft-binding service returned no binding reference")
        return BoundAudio(marked, ref, str(obj.get("scheme") or "vaak-soft-binding-v1"))
