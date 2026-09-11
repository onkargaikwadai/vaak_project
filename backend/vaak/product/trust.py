from __future__ import annotations

import base64
from dataclasses import dataclass

from ..provenance import AnchorAuthority
from ..util import canon


@dataclass(frozen=True)
class HTTPHSMKeyConfig:
    base_url: str
    key_ref: str
    api_key: str | None = None
    timeout_s: float = 8.0


class HTTPHSMKey:
    """Remote signing-key facade for Entrust/nShield-backed key services.

    The service contract intentionally keeps private key material outside Vaak.
    The configured service is expected to enforce HSM policy around ``key_ref``.
    """

    def __init__(self, config: HTTPHSMKeyConfig):
        self.config = config
        self._public_pem: str | None = None

    def _headers(self):
        return {"Authorization": f"Bearer {self.config.api_key}"} if self.config.api_key else {}

    @property
    def public_pem(self) -> str:
        if self._public_pem is None:
            import httpx
            with httpx.Client(timeout=self.config.timeout_s) as client:
                r = client.get(
                    f"{self.config.base_url.rstrip('/')}/v1/keys/{self.config.key_ref}/public",
                    headers=self._headers(),
                )
                r.raise_for_status()
                self._public_pem = str(r.json()["public_key_pem"])
        return self._public_pem

    def sign(self, data: bytes) -> str:
        import httpx
        with httpx.Client(timeout=self.config.timeout_s) as client:
            r = client.post(
                f"{self.config.base_url.rstrip('/')}/v1/keys/{self.config.key_ref}/sign",
                json={"data_b64": base64.b64encode(data).decode(), "algorithm": "Ed25519"},
                headers=self._headers(),
            )
            r.raise_for_status()
            return str(r.json()["signature_hex"])

    def wrap(self, data: bytes, *, context: str) -> bytes:
        import httpx
        with httpx.Client(timeout=self.config.timeout_s) as client:
            r = client.post(
                f"{self.config.base_url.rstrip('/')}/v1/keys/{self.config.key_ref}/wrap",
                json={"data_b64": base64.b64encode(data).decode(), "context": context},
                headers=self._headers(),
            )
            r.raise_for_status()
            return base64.b64decode(r.json()["wrapped_b64"])

    def unwrap(self, wrapped: bytes, *, context: str) -> bytes:
        import httpx
        with httpx.Client(timeout=self.config.timeout_s) as client:
            r = client.post(
                f"{self.config.base_url.rstrip('/')}/v1/keys/{self.config.key_ref}/unwrap",
                json={"wrapped_b64": base64.b64encode(wrapped).decode(), "context": context},
                headers=self._headers(),
            )
            r.raise_for_status()
            return base64.b64decode(r.json()["data_b64"])


class RemoteAnchorAuthority:
    def __init__(self, key: HTTPHSMKey):
        self.key = key

    @property
    def public_key_pem(self) -> str:
        return self.key.public_pem

    def sign(self, statement: dict) -> str:
        return self.key.sign(canon(statement))


@dataclass(frozen=True)
class AnchorPublisherConfig:
    base_url: str
    api_key: str | None = None
    timeout_s: float = 8.0


class HTTPAnchorPublisher:
    """Publish signed roots to Chrysalis or a compatible durable finality API."""
    def __init__(self, config: AnchorPublisherConfig):
        self.config = config

    def publish(self, statement: dict, signature_hex: str) -> str:
        import httpx
        headers = {"Authorization": f"Bearer {self.config.api_key}"} if self.config.api_key else {}
        with httpx.Client(timeout=self.config.timeout_s) as client:
            r = client.post(
                f"{self.config.base_url.rstrip('/')}/v1/anchors",
                json={"statement": statement, "signature": signature_hex},
                headers=headers,
            )
            r.raise_for_status()
            return str(r.json()["anchor_ref"])
