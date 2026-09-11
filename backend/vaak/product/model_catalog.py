from __future__ import annotations

import json
from dataclasses import dataclass

from ..media.gateway import MediaGatewayASR, MediaGatewayConfig, MediaGatewayTTS
from ..media.local_asr import FasterWhisperASR, FasterWhisperConfig
from ..media.qwen_tts import Qwen3TTSBackend, Qwen3TTSConfig


@dataclass(frozen=True)
class ModelProvider:
    provider_id: str
    family: str
    role: str
    endpoint: str | None = None
    api_key: str | None = None
    enabled: bool = True
    local: bool = False
    model_id: str | None = None
    open_weights: bool = False
    commercial_use_allowed: bool = False
    license_id: str | None = None
    weights_uri: str | None = None

    @property
    def production_eligible(self) -> bool:
        return bool(
            self.enabled
            and self.open_weights
            and self.commercial_use_allowed
            and self.license_id
            and self.weights_uri
            and self.model_id
        )


DEFAULT_MODEL_FAMILIES = (
    {
        "provider_id": "qwen3_tts",
        "family": "Qwen3-TTS",
        "role": "tts",
        "open_weights": True,
        "commercial_use_allowed": True,
        "license_id": "Apache-2.0",
    },
    {
        "provider_id": "nvidia_nemotron_tts",
        "family": "NVIDIA Nemotron Speech TTS",
        "role": "tts",
        "open_weights": True,
        "commercial_use_allowed": True,
        "license_id": "OpenMDW/checkpoint-specific",
    },
    {
        "provider_id": "nvidia_nemotron_asr",
        "family": "NVIDIA Nemotron 3.5 ASR",
        "role": "asr",
        "open_weights": True,
        "commercial_use_allowed": True,
        "license_id": "OpenMDW-1.1",
    },
    {
        "provider_id": "whisper_asr",
        "family": "Whisper",
        "role": "asr",
        "open_weights": True,
        "commercial_use_allowed": True,
        "license_id": "MIT",
    },
    {
        "provider_id": "mistral_voxtral_asr",
        "family": "Mistral Voxtral",
        "role": "asr",
        "open_weights": True,
        "commercial_use_allowed": True,
        "license_id": "Apache-2.0",
    },
    {
        "provider_id": "meta_llama_agency",
        "family": "Meta Llama",
        "role": "agency",
        "open_weights": True,
        "commercial_use_allowed": True,
        "license_id": "Llama-Community-License/checkpoint-specific",
    },
    {
        "provider_id": "deepseek_agency",
        "family": "DeepSeek",
        "role": "agency",
        "open_weights": True,
        "commercial_use_allowed": True,
        "license_id": "DeepSeek-Model-License/checkpoint-specific",
    },
)


def parse_catalog(raw: str | None) -> list[ModelProvider]:
    if not raw:
        return []
    obj = json.loads(raw)
    if not isinstance(obj, list):
        raise ValueError("VAAK_MODEL_CATALOG_JSON must be a JSON array")
    return [ModelProvider(**item) for item in obj]


def build_asr_providers(catalog: list[ModelProvider]):
    out = {}
    for p in catalog:
        if not p.enabled or p.role != "asr":
            continue
        if p.provider_id == "whisper_asr" and p.local:
            out[p.provider_id] = FasterWhisperASR(FasterWhisperConfig(model=p.model_id or "large-v3-turbo"))
        elif p.endpoint:
            out[p.provider_id] = MediaGatewayASR(MediaGatewayConfig(p.endpoint, p.api_key))
    return out


def build_tts_providers(catalog: list[ModelProvider]):
    out = {}
    for p in catalog:
        if not p.enabled or p.role != "tts":
            continue
        if p.provider_id == "qwen3_tts" and p.local:
            out[p.provider_id] = Qwen3TTSBackend(Qwen3TTSConfig(model_id=p.model_id or "Qwen/Qwen3-TTS-12Hz-1.7B-Base"))
        elif p.endpoint:
            out[p.provider_id] = MediaGatewayTTS(MediaGatewayConfig(p.endpoint, p.api_key))
    return out
