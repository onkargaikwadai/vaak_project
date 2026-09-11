"""vaak.session — the authorized synthesis path, end to end.

A VoiceSession is the ONLY way audio leaves this daemon. Opening one
requires: a valid, unexpired, unrevoked leaf credential bound to the exact
voice model; an envelope authorization for the verb/channel; a consent
record covering the requested language; and (on CALL channels with
disclosure required) the disclosure event before any content frame.
Every emitted chunk is watermarked. Barge-in halts the stream within one
chunk boundary. Closing emits a chained SessionManifest.
"""
from __future__ import annotations

import hashlib
import secrets
import time
from typing import Iterator

from .identity import Intermediate, LeafCredential, CredentialError, SubjectKeypair
from .enrollment import EnrollmentService, EnrollmentError
from .envelope import Authorization, ChannelClass, EnvelopeError, VoiceVerb
from .agency import (
    AgencyContext,
    AgencyIntegrity,
    Beneficiary,
    UndertoneAnalyzer,
    UndertoneObservation,
    AgencyClassifier,
)
from .provenance import ProvenanceLog, SessionManifest
from .synthesis import SAMPLE_RATE, SynthesisBackend, SynthesisRequest
from .modeling import VaakModelPlan
from .vault import VoiceVault
from . import watermark
from .util import canon, sha256_hex


class SessionError(Exception):
    pass


class VoiceSession:
    def __init__(
        self,
        *,
        twin_id: str,
        credential: LeafCredential,
        authorization: Authorization,
        intermediate: Intermediate,
        vault: VoiceVault,
        enrollment: EnrollmentService,
        backend: SynthesisBackend,
        log: ProvenanceLog,
        asset_id: str,
        owner_public_key_pem: str,
        subject: SubjectKeypair | None = None,
        language: str = "en",
        undertone_analyzer: UndertoneAnalyzer | None = None,
        agency_classifier: AgencyClassifier | None = None,
        reference_text: str | None = None,
        identity_context: dict | None = None,
        undertone_buffer_ms: int = 120,
        undertone_window_ms: int = 300,
        soft_binder=None,
    ):
        # --- admission checks: all must pass before a single frame exists ---
        admitted_at = time.time()
        intermediate.verify(credential, at=admitted_at)  # raises CredentialError
        if credential.twin_id != twin_id:
            raise SessionError("credential twin mismatch")
        consent = enrollment.consent_for(asset_id)  # raises EnrollmentError
        if consent.voice_model_hash != credential.voice_model_hash:
            raise SessionError("credential not bound to this voice model")
        if language not in consent.enabled_languages:
            raise SessionError(f"language '{language}' not enabled by owner")

        self.session_id = secrets.token_hex(16)
        self._subject = subject  # credential-certified session keypair
        self._intermediate = intermediate
        self.twin_id = twin_id
        self._credential = credential
        self._auth = authorization
        self._vault = vault
        self._backend = backend
        self._log = log
        self._asset_id = asset_id
        self._owner_public_key_pem = owner_public_key_pem
        self._voice_model_hash = credential.voice_model_hash
        self._language = language
        self._reference_text = reference_text
        self.identity_context = dict(identity_context or {"identity_class": "developer_agent", "principal_id": twin_id})
        self._last_credential_valid_at = admitted_at
        self._last_status_proof = intermediate.status_proof(credential, at=admitted_at)
        default_beneficiary = (
            Beneficiary.OWNER
            if authorization.channel == ChannelClass.LOCAL
            and authorization.verb == VoiceVerb.SPEAK_PRIVATE
            else Beneficiary.UNKNOWN
        )
        self._undertone_buffer_ms = max(50, int(undertone_buffer_ms))
        self._undertone_window_ms = max(
            self._undertone_buffer_ms, int(undertone_window_ms)
        )
        self._undertone_buffer_bytes = int(
            SAMPLE_RATE * 2 * self._undertone_buffer_ms / 1000
        )
        self._agency = AgencyIntegrity(
            authorization.agency_policy,
            analyzer=undertone_analyzer,
            classifier=agency_classifier,
            default_beneficiary=default_beneficiary,
            output_window_ms=self._undertone_window_ms,
        )

        self._digest = hashlib.sha256()
        self._frames = 0
        self._barge_in = False
        self._closed = False
        self._termination_reason: str | None = None
        self._disclosed = False
        self._disclosure_evidence: dict | None = None
        self._disclosure_audio_sha256: str | None = None
        self._needs_disclosure = (authorization.channel == ChannelClass.CALL)
        self._soft_binder = soft_binder
        self._soft_binding_refs: list[str] = []

    # -- disclosure (policy posture: default-on for call channels) --------
    def disclose(self, evidence: dict | None = None, *, audio_sha256: str | None = None) -> None:
        if self._needs_disclosure and self.identity_context.get("require_delivery_evidence") and not evidence:
            raise SessionError("trusted disclosure delivery evidence required")
        self._disclosure_evidence = dict(evidence or {})
        self._disclosure_audio_sha256 = audio_sha256
        self._disclosed = True

    def speak_disclosure(self, text: str) -> Iterator[bytes]:
        """Render the mandatory call disclosure before normal speech.

        Rendering alone does not satisfy disclosure. The transport must return
        trusted delivery evidence bound to the rendered PCM hash; only then is
        ``disclose`` called and normal call speech unlocked.
        """
        if not self._needs_disclosure:
            raise SessionError("session does not require call disclosure")
        yield from self._speak(text, prosody={"urgency": 0.0, "dominance": 0.0}, agency={"intent": "inform"}, allow_undisclosed=True)

    # -- barge-in ----------------------------------------------------------
    def barge_in(self) -> None:
        self._barge_in = True

    def _assert_credential_current(self, at: float | None = None) -> None:
        """Revalidate the live leaf credential. Admission is not enough:
        revocation or expiry during an open session must stop speech and
        attestation at the next policy boundary."""
        checked_at = time.time() if at is None else at
        self._intermediate.verify(self._credential, at=checked_at)
        self._last_credential_valid_at = checked_at
        self._last_status_proof = self._intermediate.status_proof(
            self._credential, at=checked_at
        )
        if self.identity_context.get("identity_class") == "studio_character":
            rights = self.identity_context.get("rights_grant") or {}
            expires_at = rights.get("expires_at")
            if isinstance(expires_at, (int, float)) and checked_at >= expires_at:
                raise SessionError("studio rights grant expired")
            if rights.get("revoked_at") is not None and checked_at >= float(rights["revoked_at"]):
                raise SessionError("studio rights grant revoked")

    # -- speaking ----------------------------------------------------------
    def speak(
        self,
        text: str,
        prosody: dict | None = None,
        agency: dict | AgencyContext | None = None,
    ) -> Iterator[bytes]:
        yield from self._speak(text, prosody=prosody, agency=agency, allow_undisclosed=False)

    def _speak(
        self,
        text: str,
        prosody: dict | None = None,
        agency: dict | AgencyContext | None = None,
        *,
        allow_undisclosed: bool = False,
    ) -> Iterator[bytes]:
        if self._closed:
            raise SessionError("session closed")
        self._assert_credential_current()
        if self._needs_disclosure and not self._disclosed and not allow_undisclosed:
            raise SessionError("call disclosure required before speaking")
        declared_context = (
            agency if isinstance(agency, AgencyContext) else AgencyContext.from_body(agency)
        )
        context = self._agency.inspect_pre_synthesis(
            text, declared_context, prosody
        )
        model = self._vault.load(self._asset_id)  # raises if shredded
        wm_key = self._vault.detection_key(self._voice_model_hash)
        beacon = hashlib.sha256(
            f"{self.twin_id}|{self.session_id}".encode()
        ).digest()[: watermark.BEACON_BYTES]
        request = SynthesisRequest(
            text=text, voice_model=model, language=self._language, prosody=prosody,
            voice_id=self._asset_id, voice_model_hash=self._voice_model_hash,
            reference_text=self._reference_text,
            conditioning=dict(self.identity_context.get("conditioning") or {}),
        )
        output_allow_recorded = False
        pending: list[bytes] = []
        pending_bytes = 0

        def emit_pending() -> Iterator[bytes]:
            nonlocal pending, pending_bytes, output_allow_recorded
            if not pending:
                return
            # A bounded acoustic safety buffer lets the undertone analyzer see
            # temporal contour before any PCM in this window is released.
            window = b"".join(pending)
            self._agency.inspect_output(
                window,
                prosody=prosody,
                context=context,
                record_allow=not output_allow_recorded,
            )
            output_allow_recorded = True
            ready = pending
            pending = []
            pending_bytes = 0
            for raw in ready:
                # Revocation/erasure may arrive while a buffered window is
                # waiting. Re-check immediately before each actual emission.
                self._assert_credential_current()
                if self._barge_in:
                    return
                if self._soft_binder is not None:
                    bound = self._soft_binder.bind(
                        raw,
                        sample_rate=SAMPLE_RATE,
                        context={
                            "tenant_id": self.identity_context.get("tenant_id"),
                            "principal_id": self.identity_context.get("principal_id", self.twin_id),
                            "session_id": self.session_id,
                            "voice_model_hash": self._voice_model_hash,
                            "envelope_hash": self._auth.envelope_hash,
                            "frame_offset": self._frames,
                        },
                    )
                    raw = bound.pcm16
                    self._soft_binding_refs.append(bound.binding_ref)
                # A compact reference beacon remains useful for local/offline
                # resolution; production soft binding is the robust layer.
                marked = watermark.embed(
                    raw,
                    wm_key,
                    frame_offset=self._frames,
                    beacon=beacon,
                )
                self._digest.update(marked)
                self._frames += len(marked) // 2 // watermark.FRAME_SAMPLES
                yield marked

        for chunk in self._backend.stream(request):
            self._assert_credential_current()
            if self._barge_in:
                self._barge_in = False
                return
            pending.append(chunk)
            pending_bytes += len(chunk)
            if pending_bytes >= self._undertone_buffer_bytes:
                yield from emit_pending()
                if self._barge_in:
                    self._barge_in = False
                    return
        yield from emit_pending()
        if self._barge_in:
            self._barge_in = False
            return

    # -- Agency Integrity / incoming undertone ----------------------------
    def observe_incoming_undertone(self, observation: dict | UndertoneObservation) -> None:
        obs = observation if isinstance(observation, UndertoneObservation) else (
            UndertoneObservation.from_body(observation, analyzer="media-input")
        )
        self._agency.observe_incoming(obs)

    def record_recipient_refusal(self) -> None:
        self._agency.record_refusal()

    def record_material_change(self, evidence_ref: str) -> None:
        self._agency.record_material_change(evidence_ref)

    def agency_report(self) -> dict:
        return self._agency.report()

    # -- live attestation --------------------------------------------------
    def attest(self, challenge_nonce: str) -> dict:
        """Challenge-response, mid-call: any counterparty may challenge the
        twin to prove — live, cryptographically — that this session is an
        authorized twin utterance. The attestation binds the caller's nonce
        to the running content digest, the credential, and the envelope in
        force, signed by the credential-certified session key. A mere
        acoustic imitation without the session key cannot produce the same
        signature, and a revoked credential is refused before signing.
        Independent verification requires the separately pinned trust bundle.
        """
        if self._closed:
            raise SessionError("session closed")
        if self._subject is None:
            raise SessionError("session opened without attestation keypair")
        if not challenge_nonce or len(challenge_nonce) > 512:
            raise SessionError("invalid challenge nonce")
        checked_at = time.time()
        self._assert_credential_current(at=checked_at)
        statement = {
            "type": "vaak-live-attestation-v1",
            "twin_id": self.twin_id,
            "session_id": self.session_id,
            "credential_id": self._credential.credential_id,
            "envelope_hash": self._auth.envelope_hash,
            "register": self._auth.register.value,
            "challenge_nonce": challenge_nonce,
            "content_digest_so_far": self._digest.hexdigest(),
            "frames_so_far": self._frames,
            "credential_valid_at": checked_at,
            "credential_expires_at": self._credential.expires_at,
            "agency_policy_hash": self._auth.agency_policy.policy_hash(),
            "agency_events_digest": self._agency.event_digest(),
            "identity_class": self.identity_context.get("identity_class", "developer_agent"),
            "principal_id": self.identity_context.get("principal_id", self.twin_id),
            "identity_context_hash": sha256_hex(canon(self.identity_context)),
            "attested_at": checked_at,
        }
        envelope_evidence = dict(
            self._auth.envelope_body,
            owner_signature=self._auth.envelope_owner_signature,
        )
        return {
            "statement": statement,
            "signature": self._subject.sign(canon(statement)),
            "credential": self._credential.body()
            | {"issuer_signature": self._credential.issuer_signature},
            "credential_status": self._last_status_proof,
            "envelope": envelope_evidence,
        }

    # -- close → manifest --------------------------------------------------
    def close(self) -> SessionManifest:
        if self._closed:
            raise SessionError("already closed")
        self._closed = True
        credential_evidence = self._credential.body() | {
            "issuer_signature": self._credential.issuer_signature
        }
        envelope_evidence = dict(
            self._auth.envelope_body,
            owner_signature=self._auth.envelope_owner_signature,
        )
        authorization_evidence = {
            "type": "vaak-authorization-evidence-v1",
            "credential": credential_evidence,
            "issuer_public_key_pem": self._intermediate.public_key_pem,
            "envelope": envelope_evidence,
            "owner_public_key_pem": self._owner_public_key_pem,
            "credential_status": self._last_status_proof,
            "agency_events": [e.body() for e in self._agency.events],
            "identity_context": self.identity_context,
            "soft_binding_refs": list(self._soft_binding_refs),
            "presence_evidence": self.identity_context.get("presence_evidence"),
            "disclosure_evidence": self._disclosure_evidence,
            "disclosure_audio_sha256": self._disclosure_audio_sha256,
        }
        evidence_hash = sha256_hex(canon(authorization_evidence))
        manifest = SessionManifest(
            twin_id=self.twin_id,
            session_id=self.session_id,
            credential_id=self._credential.credential_id,
            voice_model_hash=self._credential.voice_model_hash,
            envelope_version=self._auth.envelope_version,
            envelope_hash=self._auth.envelope_hash,
            verb=self._auth.verb.value,
            channel=self._auth.channel.value,
            register=self._auth.register.value,
            presence_checked=self._auth.presence_checked,
            frames_emitted=self._frames,
            content_digest=self._digest.hexdigest(),
            disclosed=self._disclosed,
            last_credential_valid_at=self._last_credential_valid_at,
            agency_policy_hash=self._auth.agency_policy.policy_hash(),
            agency_event_count=len(self._agency.events),
            agency_events_digest=self._agency.event_digest(),
            evidence_hash=evidence_hash,
            termination_reason=self._termination_reason,
            closed_at=time.time(),
            prev_hash=self._log.head(self.twin_id),
            identity_class=str(self.identity_context.get("identity_class", "developer_agent")),
            principal_id=str(self.identity_context.get("principal_id", self.twin_id)),
            identity_context_hash=sha256_hex(canon(self.identity_context)),
            rights_grant_hash=self.identity_context.get("rights_grant_hash"),
            production_id=(self.identity_context.get("production") or {}).get("production_id"),
            performance_version=self.identity_context.get("performance_version"),
            conditioning_hash=self.identity_context.get("conditioning_hash"),
            model_plan_hash=VaakModelPlan.from_conditioning(self.identity_context.get("conditioning")).plan_hash(),
            language=self._language,
            usage_class=(self.identity_context.get("production") or {}).get("usage_class"),
            territory=(self.identity_context.get("production") or {}).get("territory"),
            tenant_id=self.identity_context.get("tenant_id"),
            presence_evidence_hash=self.identity_context.get("presence_evidence_hash"),
            disclosure_evidence_hash=(sha256_hex(canon(self._disclosure_evidence)) if self._disclosure_evidence else None),
            disclosure_audio_sha256=self._disclosure_audio_sha256,
            model_provider_id=getattr(getattr(self._backend, "backend", None), "last_provider_id", None),
            soft_binding_count=len(self._soft_binding_refs),
            soft_binding_refs_digest=sha256_hex(canon(self._soft_binding_refs)) if self._soft_binding_refs else None,
        )
        self._log.append(manifest, authorization_evidence)
        return manifest

    def terminate(self, reason: str) -> SessionManifest:
        """Force-close the session without requiring a still-valid credential.

        Used by atomic voice erasure after the associated credential has been
        revoked.  Historical evidence keeps the last issuer-signed *valid*
        status proof from before revocation; no new attestation can be made.
        """
        if self._closed:
            raise SessionError("session closed")
        self._termination_reason = str(reason)
        return self.close()
