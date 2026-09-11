from __future__ import annotations

import json
import os
from dataclasses import dataclass

from .model_catalog import parse_catalog


@dataclass(frozen=True)
class ProductConfig:
    environment: str = "development"
    allow_dev_providers: bool = False

    state_db_path: str = "/tmp/vaak/state.db"
    auth_db_path: str = "/tmp/vaak/auth.db"
    quality_db_path: str = "/tmp/vaak/quality.db"
    voice_vault_db_path: str = "/tmp/vaak/voice-vault.db"

    model_catalog_json: str | None = None
    quality_snapshots_json: str | None = None

    media_gateway_url: str | None = None
    media_gateway_key: str | None = None
    soft_binding_url: str | None = None
    soft_binding_key: str | None = None

    enrollment_verifier_url: str | None = None
    enrollment_verifier_key: str | None = None
    evidence_verifier_url: str | None = None
    evidence_verifier_key: str | None = None

    hsm_url: str | None = None
    hsm_key: str | None = None
    issuer_key_ref: str = "vaak/issuer"
    anchor_key_ref: str = "vaak/anchor"
    vault_key_ref: str = "vaak/vault-wrap"
    owner_key_prefix: str = "vaak/owner"

    chrysalis_url: str | None = None
    chrysalis_key: str | None = None

    legacy_api_keys: tuple[str, ...] = ()
    dev_evidence_secret: str | None = None

    kokoro_api_key: str | None = None
    kokoro_base_url: str = "https://bluroute.denizenblu.com"
    kokoro_model: str = "kokoro-82m"
    kokoro_voice: str = "af_bella"

    @classmethod
    def from_env(cls) -> "ProductConfig":
        env = os.getenv("VAAK_ENV", "development")
        raw_allow = os.getenv("VAAK_ALLOW_DEV_PROVIDERS")
        allow_dev = (raw_allow == "1") if raw_allow is not None else env.lower() not in {"prod", "production", "ga"}
        return cls(
            environment=env,
            allow_dev_providers=allow_dev,
            state_db_path=os.getenv("VAAK_STATE_DB", "/tmp/vaak/state.db"),
            auth_db_path=os.getenv("VAAK_AUTH_DB", "/tmp/vaak/auth.db"),
            quality_db_path=os.getenv("VAAK_QUALITY_DB", "/tmp/vaak/quality.db"),
            voice_vault_db_path=os.getenv("VAAK_VOICE_VAULT_DB", "/tmp/vaak/voice-vault.db"),
            model_catalog_json=os.getenv("VAAK_MODEL_CATALOG_JSON"),
            quality_snapshots_json=os.getenv("VAAK_QUALITY_SNAPSHOTS_JSON"),
            media_gateway_url=os.getenv("VAAK_MEDIA_GATEWAY_URL"),
            media_gateway_key=os.getenv("VAAK_MEDIA_GATEWAY_KEY"),
            soft_binding_url=os.getenv("VAAK_SOFT_BINDING_URL"),
            soft_binding_key=os.getenv("VAAK_SOFT_BINDING_KEY"),
            enrollment_verifier_url=os.getenv("VAAK_ENROLLMENT_VERIFIER_URL"),
            enrollment_verifier_key=os.getenv("VAAK_ENROLLMENT_VERIFIER_KEY"),
            evidence_verifier_url=os.getenv("VAAK_EVIDENCE_VERIFIER_URL"),
            evidence_verifier_key=os.getenv("VAAK_EVIDENCE_VERIFIER_KEY"),
            hsm_url=os.getenv("VAAK_HSM_URL"),
            hsm_key=os.getenv("VAAK_HSM_KEY"),
            issuer_key_ref=os.getenv("VAAK_ISSUER_KEY_REF", "vaak/issuer"),
            anchor_key_ref=os.getenv("VAAK_ANCHOR_KEY_REF", "vaak/anchor"),
            vault_key_ref=os.getenv("VAAK_VAULT_KEY_REF", "vaak/vault-wrap"),
            owner_key_prefix=os.getenv("VAAK_OWNER_KEY_PREFIX", "vaak/owner"),
            chrysalis_url=os.getenv("VAAK_CHRYSALIS_URL"),
            chrysalis_key=os.getenv("VAAK_CHRYSALIS_KEY"),
            legacy_api_keys=tuple(k.strip() for k in os.getenv("VAAK_API_KEYS", "").split(",") if k.strip()),
            dev_evidence_secret=os.getenv("VAAK_DEV_EVIDENCE_SECRET"),
            kokoro_api_key=os.getenv("BLU_KEY") or os.getenv("KOKORO_API_KEY"),
            kokoro_base_url=os.getenv("KOKORO_BASE_URL", "https://bluroute.denizenblu.com"),
            kokoro_model=os.getenv("KOKORO_MODEL", "kokoro-82m"),
            kokoro_voice=os.getenv("KOKORO_VOICE", "af_bella"),
        )

    @property
    def production(self) -> bool:
        return self.environment.lower() in {"prod", "production", "ga"}

    def validate(self) -> None:
        if self.production:
            missing = []
            for name, value in (
                ("VAAK_STATE_DB", self.state_db_path),
                ("VAAK_AUTH_DB", self.auth_db_path),
                ("VAAK_QUALITY_DB", self.quality_db_path),
                ("VAAK_VOICE_VAULT_DB", self.voice_vault_db_path),
                ("VAAK_MODEL_CATALOG_JSON", self.model_catalog_json),
                ("VAAK_QUALITY_SNAPSHOTS_JSON", self.quality_snapshots_json),
                ("VAAK_MEDIA_GATEWAY_URL", self.media_gateway_url),
                ("VAAK_SOFT_BINDING_URL", self.soft_binding_url),
                ("VAAK_ENROLLMENT_VERIFIER_URL", self.enrollment_verifier_url),
                ("VAAK_EVIDENCE_VERIFIER_URL", self.evidence_verifier_url),
                ("VAAK_HSM_URL", self.hsm_url),
                ("VAAK_CHRYSALIS_URL", self.chrysalis_url),
            ):
                if not value:
                    missing.append(name)
            if missing:
                raise RuntimeError("production config missing: " + ", ".join(missing))
            if self.allow_dev_providers:
                raise RuntimeError("development providers are prohibited in production")
            if self.legacy_api_keys:
                raise RuntimeError("legacy shared API keys are prohibited in production")

            catalog = parse_catalog(self.model_catalog_json)
            enabled = [p for p in catalog if p.enabled]
            roles = {p.role for p in enabled}
            if "tts" not in roles or "asr" not in roles or "agency" not in roles:
                raise RuntimeError("production model catalog must contain tts, asr and agency providers")

            # Vaak GA is open-weight-only. Production providers must identify a
            # downloadable checkpoint, its license, and Parinita's commercial-use
            # eligibility. A hosted-only/opaque model cannot be enabled.
            for provider in enabled:
                if not provider.open_weights:
                    raise RuntimeError(f"production model provider is not open-weight: {provider.provider_id}")
                if not provider.commercial_use_allowed:
                    raise RuntimeError(f"production model provider lacks commercial-use rights: {provider.provider_id}")
                if not provider.license_id:
                    raise RuntimeError(f"production model provider missing license metadata: {provider.provider_id}")
                if not provider.weights_uri or not provider.model_id:
                    raise RuntimeError(f"production model provider must pin downloadable weights: {provider.provider_id}")

            agency_families = {p.family for p in enabled if p.role == "agency"}
            if len(agency_families) < 2:
                raise RuntimeError("production Agency Integrity requires at least two independently configurable model families")
            required_families = {
                "Qwen3-TTS",
                "NVIDIA Nemotron 3.5 ASR", "Whisper", "Mistral Voxtral",
                "Meta Llama", "DeepSeek",
            }
            enabled_families = {p.family for p in enabled}
            missing_families = sorted(required_families - enabled_families)
            if missing_families:
                raise RuntimeError("production model catalog missing required open-weight families: " + ", ".join(missing_families))
            snapshots = json.loads(self.quality_snapshots_json or "[]")
            if not snapshots:
                raise RuntimeError("production requires measured model quality snapshots")
            snap_ids = {str(s.get("provider_id")) for s in snapshots if isinstance(s, dict)}
            enabled_ids = {p.provider_id for p in catalog if p.enabled}
            missing_snapshots = sorted(enabled_ids - snap_ids)
            if missing_snapshots:
                raise RuntimeError("production quality snapshots missing providers: " + ", ".join(missing_snapshots))
