from __future__ import annotations

import json

from ..daemon import VaakCore
from ..identity import Intermediate
from ..media import (
    DeterministicASR,
    DeterministicTTS,
    DeterministicUndertone,
    EnergyVAD,
    KokoroTTSBackend,
    KokoroTTSConfig,
    MediaGatewayConfig,
    MediaGatewayUndertone,
    MediaGatewayVAD,
)
from ..media.ensemble import QualityRouter, QualitySnapshot, QualityStore, RoutedASR, RoutedTTS
from ..media.binding import HTTPSoftBindingBackend, SoftBindingConfig
from ..provenance import ProvenanceLog
from ..vault import DurableVoiceVault, LocalAESKeyWrapper
from ..studio import StudioRightsService, StudioRuntime
from .adapters import AsyncMediaSynthesisBackend, SyncUndertoneAnalyzer
from .api import create_app
from .classifier import AgencyClassifierConfig, EnsembleAgencyClassifier, HTTPAgencyClassifier
from .config import ProductConfig
from .evidence import (
    DevEnrollmentVerifier,
    HMACDisclosureVerifier,
    HMACPresenceVerifier,
    HTTPDisclosureVerifier,
    HTTPEnrollmentVerifier,
    HTTPIdentityVerifier,
    HMACIdentityVerifier,
    HTTPEnrollmentVerifierConfig,
    HTTPEvidenceConfig,
    HTTPPresenceVerifier,
)
from .model_catalog import build_asr_providers, build_tts_providers, parse_catalog
from .runtime import ProductRuntime
from .security import TenantAuthStore
from .state import DurableProductState
from .trust import AnchorPublisherConfig, HTTPAnchorPublisher, HTTPHSMKey, HTTPHSMKeyConfig, RemoteAnchorAuthority


def _quality_router(config: ProductConfig) -> QualityRouter:
    store = QualityStore(config.quality_db_path)
    if config.quality_snapshots_json:
        for obj in json.loads(config.quality_snapshots_json):
            store.put(QualitySnapshot(**obj))
    return QualityRouter(store)


def build_runtime(config: ProductConfig) -> ProductRuntime:
    config.validate()
    router = _quality_router(config)
    catalog = parse_catalog(config.model_catalog_json)

    asr_providers = build_asr_providers(catalog)
    tts_providers = build_tts_providers(catalog)

    if asr_providers and tts_providers:
        # Raises at boot if no configured provider clears the measured quality floor.
        router.ranked("asr", list(asr_providers))
        router.ranked("tts", list(tts_providers))
        asr = RoutedASR(asr_providers, router)
        tts = RoutedTTS(tts_providers, router)
    else:
        if not config.allow_dev_providers:
            raise RuntimeError("production-quality ASR and TTS providers are not configured")
        asr = DeterministicASR()
        if config.kokoro_api_key:
            tts = KokoroTTSBackend(KokoroTTSConfig(
                api_key=config.kokoro_api_key,
                base_url=config.kokoro_base_url,
                model=config.kokoro_model,
                voice=config.kokoro_voice,
            ))
        else:
            tts = DeterministicTTS()

    if config.media_gateway_url:
        media_cfg = MediaGatewayConfig(config.media_gateway_url, config.media_gateway_key)
        undertone = MediaGatewayUndertone(media_cfg)
        vad = MediaGatewayVAD(media_cfg)
    else:
        if not config.allow_dev_providers:
            raise RuntimeError("production acoustic undertone/VAD gateway is not configured")
        undertone, vad = DeterministicUndertone(), EnergyVAD(threshold=0.0)

    agency_members = []
    agency_candidates = [p for p in catalog if p.enabled and p.role == "agency" and p.endpoint]
    if agency_candidates:
        eligible = router.ranked("agency", [p.provider_id for p in agency_candidates])
        by_id = {p.provider_id: p for p in agency_candidates}
        for provider_id in eligible:
            p = by_id[provider_id]
            agency_members.append(HTTPAgencyClassifier(AgencyClassifierConfig(
                p.endpoint or "", p.api_key, classifier_name=f"{p.family}:{provider_id}"
            )))
    if agency_members:
        classifier = EnsembleAgencyClassifier(agency_members)
    elif config.allow_dev_providers:
        classifier = None
    else:
        raise RuntimeError("no quality-qualified independent Agency Integrity classifier configured")

    # Development runtimes default to process memory for product state so tests
    # and local sessions do not inherit stale principals from earlier runs.
    # An explicit non-default path still enables restart-persistence testing.
    state = None if (not config.production and config.state_db_path == "/tmp/vaak/state.db") else DurableProductState(config.state_db_path)

    if config.production:
        issuer_key = HTTPHSMKey(HTTPHSMKeyConfig(config.hsm_url or "", config.issuer_key_ref, config.hsm_key))
        anchor_key = HTTPHSMKey(HTTPHSMKeyConfig(config.hsm_url or "", config.anchor_key_ref, config.hsm_key))
        vault_key = HTTPHSMKey(HTTPHSMKeyConfig(config.hsm_url or "", config.vault_key_ref, config.hsm_key))
        intermediate = Intermediate(signer=issuer_key, state_path=config.state_db_path)
        vault = DurableVoiceVault(config.voice_vault_db_path, vault_key)
        publisher = HTTPAnchorPublisher(AnchorPublisherConfig(config.chrysalis_url or "", config.chrysalis_key))
        log = ProvenanceLog(authority=RemoteAnchorAuthority(anchor_key), publisher=publisher, auto_publish=True)

        def owner_key_factory(principal_id: str):
            ref = f"{config.owner_key_prefix}/{principal_id}"
            return HTTPHSMKey(HTTPHSMKeyConfig(config.hsm_url or "", ref, config.hsm_key))

        enrollment_verifier = HTTPEnrollmentVerifier(HTTPEnrollmentVerifierConfig(config.enrollment_verifier_url or "", config.enrollment_verifier_key))
        evcfg = HTTPEvidenceConfig(config.evidence_verifier_url or "", config.evidence_verifier_key)
        presence_verifier = HTTPPresenceVerifier(evcfg)
        disclosure_verifier = HTTPDisclosureVerifier(evcfg)
        identity_verifier = HTTPIdentityVerifier(evcfg)
        soft_binder = HTTPSoftBindingBackend(SoftBindingConfig(config.soft_binding_url or "", config.soft_binding_key))
    else:
        intermediate = None
        log = None
        owner_key_factory = None
        enrollment_verifier = DevEnrollmentVerifier()
        secret = (config.dev_evidence_secret or "vaak-development-evidence-secret-32-bytes-minimum").encode()
        if len(secret) < 32:
            secret = secret.ljust(32, b"0")
        presence_verifier = HMACPresenceVerifier(secret)
        disclosure_verifier = HMACDisclosureVerifier(secret)
        identity_verifier = HMACIdentityVerifier(secret)
        if state is not None:
            vault_path = config.voice_vault_db_path
            if vault_path == "/tmp/vaak/voice-vault.db":
                vault_path = config.state_db_path + ".vault"
            vault = DurableVoiceVault(vault_path, LocalAESKeyWrapper(secret))
        else:
            vault = None
        soft_binder = None

    core = VaakCore(
        backend=AsyncMediaSynthesisBackend(tts),
        undertone_analyzer=SyncUndertoneAnalyzer(undertone),
        agency_classifier=classifier,
        intermediate=intermediate,
        owner_key_factory=owner_key_factory,
        log=log,
        vault=vault,
        soft_binder=soft_binder,
    )
    runtime = ProductRuntime(
        core=core,
        asr=asr,
        input_undertone=undertone,
        vad=vad,
        state=state,
        enrollment_verifier=enrollment_verifier,
        presence_verifier=presence_verifier,
        disclosure_verifier=disclosure_verifier,
        quality_router=router,
    )
    rights = StudioRightsService(key_factory=owner_key_factory) if owner_key_factory else StudioRightsService()
    runtime.studio = StudioRuntime(runtime, rights=rights, identity_verifier=identity_verifier, require_identity_evidence=config.production)
    return runtime


def build_app(config: ProductConfig | None = None):
    cfg = config or ProductConfig.from_env()
    runtime = build_runtime(cfg)
    auth = TenantAuthStore(cfg.auth_db_path)
    return create_app(runtime, auth=auth, api_keys=cfg.legacy_api_keys)
