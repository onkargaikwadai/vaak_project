from __future__ import annotations

import asyncio
import base64
import hashlib
import queue
import threading
from dataclasses import dataclass

from ..agency import AgencyContext, AgencyPolicy, UndertoneObservation
from ..daemon import VaakCore
from ..enrollment import ConsentRecord
from ..envelope import ChannelClass, EnvelopeEnforcer, Register, VoiceEnvelope, VoiceVerb
from ..registry import DeathCertificate
from ..voice_model import VoiceConditioning
from ..media.contracts import ASRBackend, AcousticUndertoneBackend, VADBackend
from .evidence import DisclosureVerifier, EnrollmentVerifier, PresenceVerifier
from .principals import PrincipalProfile
from .state import DurableProductState


DEFAULT_CALL_DISCLOSURE = "You are speaking with an AI-generated voice operating under authorized Vaak identity and policy controls."


@dataclass
class LiveSession:
    session_id: str
    principal_id: str
    tenant_id: str = "default"
    mode: str = "external_agent"
    language: str = "en"
    sample_rate: int = 16000
    channel: ChannelClass = ChannelClass.LOCAL


class ProductRuntime:
    """Standalone Vaak voice/presence runtime.

    Vaak owns media, voice identity, authorization, Agency Integrity, rights,
    proof, revocation and realtime transport. Cognition is always external.
    Atma and third-party agents integrate over the same API/event contract.
    """

    def __init__(
        self,
        *,
        core: VaakCore,
        asr: ASRBackend,
        input_undertone: AcousticUndertoneBackend,
        vad: VADBackend,
        state: DurableProductState | None = None,
        enrollment_verifier: EnrollmentVerifier | None = None,
        presence_verifier: PresenceVerifier | None = None,
        disclosure_verifier: DisclosureVerifier | None = None,
        quality_router=None,
    ):
        self.core = core
        self.asr = asr
        self.input_undertone = input_undertone
        self.vad = vad
        self.state = state
        self.enrollment_verifier = enrollment_verifier
        self.presence_verifier = presence_verifier
        self.disclosure_verifier = disclosure_verifier
        self.quality_router = quality_router
        self.live: dict[str, LiveSession] = {}
        self.principals: dict[str, PrincipalProfile] = {}
        self.pending_disclosures: dict[str, dict] = {}
        self.studio = None
        if self.state:
            for p in self.state.list_principals():
                self.principals[p.principal_id] = p
            self._restore_core_state()


    def _restore_core_state(self) -> None:
        """Rehydrate durable authorization state after a process restart.

        Active sessions are intentionally not resumed; a restarted node must
        establish new short-lived session credentials. Enrolled assets, consent,
        envelopes and terminal lifecycle state survive.
        """
        if not self.state:
            return
        for row in self.state.list_voice_bindings():
            c = dict(row["consent"])
            c["enabled_languages"] = tuple(c.get("enabled_languages", ["en"]))
            consent = ConsentRecord(**c)
            self.core.assets[row["principal_id"]] = row["asset_id"]
            self.core.enrollment._consents[row["asset_id"]] = consent
            self.core.registry.register(
                row["principal_id"], row["asset_id"], consent.voice_model_hash, consent.enrolled_at
            )
        for row in self.state.list_envelopes():
            obj = dict(row["envelope"])
            policy = AgencyPolicy.from_body(obj.get("agency_policy"))
            env = VoiceEnvelope(
                twin_id=obj["twin_id"],
                version=int(obj["version"]),
                granted=frozenset(VoiceVerb(v) for v in obj.get("granted", [])),
                high_consequence=frozenset(VoiceVerb(v) for v in obj.get("high_consequence", [])),
                registers=frozenset(Register(v) for v in obj.get("registers", [])),
                disclose_on_calls=bool(obj.get("disclose_on_calls", True)),
                agency_policy=policy,
                owner_signature=str(obj["owner_signature"]),
            )
            self.core.envelopes[row["principal_id"]] = EnvelopeEnforcer(env)
        for row in self.state.list_voice_lifecycle():
            obj = dict(row["lifecycle"])
            if obj.get("status") != "erased":
                continue
            try:
                cert_obj = dict(obj["death_certificate"])
                cert_obj["revoked_credential_ids"] = tuple(cert_obj.get("revoked_credential_ids", []))
                cert_obj["terminated_session_ids"] = tuple(cert_obj.get("terminated_session_ids", []))
                cert = DeathCertificate(**cert_obj)
                self.core.registry.mark_erased(cert.voice_model_hash, cert)
            except (KeyError, TypeError):
                # Corrupt lifecycle state is a hard operational error when the
                # affected principal is used; never silently re-authorize it.
                asset_id = self.core.assets.get(row["principal_id"])
                if asset_id:
                    try:
                        consent = self.core.enrollment.consent_for(asset_id)
                        self.core.registry.mark_revoked(consent.voice_model_hash)
                    except Exception:
                        pass

    def register_principal(self, profile: PrincipalProfile) -> PrincipalProfile:
        existing = self.principals.get(profile.principal_id)
        if existing and existing != profile:
            raise ValueError("principal already exists")
        self.principals[profile.principal_id] = profile
        if self.state:
            self.state.put_principal(profile)
            self.state.event(profile.tenant_id, "principal", profile.principal_id, "principal.registered", profile.body())
        return profile

    def get_principal(self, principal_id: str, *, tenant_id: str | None = None) -> PrincipalProfile:
        p = self.principals.get(principal_id)
        if p is None and self.state:
            p = self.state.get_principal(principal_id)
            if p:
                self.principals[principal_id] = p
        if p is None:
            raise KeyError("unknown Vaak principal")
        if tenant_id is not None and p.tenant_id != tenant_id:
            raise PermissionError("principal not visible to tenant")
        return p

    def open_voice_enrollment(self, principal_id: str, *, tenant_id: str = "default") -> dict:
        self.get_principal(principal_id, tenant_id=tenant_id)
        return self.core.enroll_open(principal_id)

    def complete_voice_enrollment(
        self,
        principal_id: str,
        *,
        session_id: str,
        pcm16: bytes,
        spoken_challenge: str,
        languages: tuple[str, ...] = ("en",),
        tenant_id: str = "default",
    ) -> dict:
        self.get_principal(principal_id, tenant_id=tenant_id)
        expected = self.core.enrollment.challenge_for(session_id)
        evidence_hash = None
        evidence_body = None
        if self.enrollment_verifier is not None:
            evidence = self.enrollment_verifier.verify(
                pcm16,
                tenant_id=tenant_id,
                principal_id=principal_id,
                expected_challenge=expected,
                claimed_challenge=spoken_challenge,
                sample_rate=16000,
            )
            evidence_body = evidence.body()
            from ..util import canon, sha256_hex
            evidence_hash = sha256_hex(canon(evidence_body))
        result = self.core.enroll_complete(principal_id, {
            "session_id": session_id,
            "voice_model_b64": base64.b64encode(pcm16).decode(),
            "spoken_challenge": spoken_challenge,
            "languages": list(languages),
            "enrollment_evidence_hash": evidence_hash,
        })
        if self.state:
            consent = self.core.enrollment.consent_for(result["asset_id"])
            consent_body = consent.body() | {"owner_signature": consent.owner_signature}
            self.state.put_voice_binding(tenant_id, principal_id, result["asset_id"], consent_body)
            self.state.event(tenant_id, "principal", principal_id, "voice.enrolled", result | {"enrollment_evidence": evidence_body})
        return result | ({"enrollment_evidence": evidence_body} if evidence_body else {})

    def set_default_envelope(self, principal_id: str, *, version: int = 1, tenant_id: str = "default") -> dict:
        self.get_principal(principal_id, tenant_id=tenant_id)
        result = self.core.set_envelope(principal_id, {
            "version": version,
            "granted": [
                VoiceVerb.SPEAK_PRIVATE.value,
                VoiceVerb.SPEAK_PUBLIC.value,
                VoiceVerb.TELEPHONY_LISTEN.value,
                VoiceVerb.TELEPHONY_SPEAK.value,
            ],
            "registers": [Register.PRIVATE.value, Register.PROFESSIONAL.value, Register.BOARD.value],
            "high_consequence": [VoiceVerb.SPEAK_PUBLIC.value, VoiceVerb.TELEPHONY_SPEAK.value],
            "disclose_on_calls": True,
        })
        if self.state:
            env = self.core.envelopes[principal_id].envelope
            self.state.put_envelope(tenant_id, principal_id, env.body() | {"owner_signature": env.owner_signature})
            self.state.event(tenant_id, "principal", principal_id, "envelope.updated", result)
        return result

    def set_studio_envelope(self, character_id: str, *, version: int = 1, tenant_id: str = "default") -> dict:
        self.get_principal(character_id, tenant_id=tenant_id)
        result = self.core.set_envelope(character_id, {
            "version": version,
            "granted": [
                VoiceVerb.SPEAK_PRIVATE.value, VoiceVerb.SPEAK_PUBLIC.value,
                VoiceVerb.TELEPHONY_LISTEN.value, VoiceVerb.TELEPHONY_SPEAK.value,
                VoiceVerb.BROADCAST.value,
            ],
            "registers": [Register.PRIVATE.value, Register.PROFESSIONAL.value, Register.BOARD.value, Register.STAGE.value],
            "high_consequence": [VoiceVerb.BROADCAST.value, VoiceVerb.TELEPHONY_SPEAK.value],
            "disclose_on_calls": True,
        })
        if self.state:
            env = self.core.envelopes[character_id].envelope
            self.state.put_envelope(tenant_id, character_id, env.body() | {"owner_signature": env.owner_signature})
            self.state.event(tenant_id, "principal", character_id, "envelope.updated", result)
        return result

    def open_live(
        self,
        principal_id: str,
        *,
        mode: str = "external_agent",
        channel: ChannelClass = ChannelClass.LOCAL,
        register: Register = Register.PRIVATE,
        language: str = "en",
        tenant_id: str = "default",
        presence_evidence: str | None = None,
        identity_context: dict | None = None,
    ) -> LiveSession:
        profile = self.get_principal(principal_id, tenant_id=tenant_id)
        if channel == ChannelClass.LOCAL:
            verb = VoiceVerb.SPEAK_PRIVATE
        elif channel == ChannelClass.CALL:
            verb = VoiceVerb.TELEPHONY_SPEAK
        else:
            verb = VoiceVerb.BROADCAST

        presence_body = None
        presence_checked = False
        if presence_evidence:
            if self.presence_verifier is None:
                raise PermissionError("presence evidence supplied but no trusted verifier is configured")
            ev = self.presence_verifier.verify(presence_evidence, tenant_id=tenant_id, principal_id=principal_id)
            presence_body = ev.unsigned_body() | {"signature": ev.signature}
            presence_checked = True

        if identity_context is None:
            cond = VoiceConditioning(
                principal_id=principal_id,
                identity_class=profile.identity_class,
                persona=profile.persona,
                register=register.value,
                channel=channel.value,
                locale=language,
                authority={"envelope_managed": True, "external_cognition": True},
            )
            identity_context = {
                "identity_class": profile.identity_class.value,
                "principal_id": principal_id,
                "controller_id": profile.controller_id,
                "tenant_id": tenant_id,
                "conditioning": cond.body(),
                "conditioning_hash": cond.conditioning_hash(),
            }
        else:
            identity_context = dict(identity_context)
            identity_context["tenant_id"] = tenant_id
        if channel == ChannelClass.CALL:
            identity_context["require_delivery_evidence"] = True
        if presence_body:
            from ..util import canon, sha256_hex
            identity_context["presence_evidence"] = presence_body
            identity_context["presence_evidence_hash"] = sha256_hex(canon(presence_body))

        sid = self.core.open_session(principal_id, {
            "verb": verb.value,
            "channel": channel.value,
            "register": register.value,
            "language": language,
            "presence_checked": presence_checked,
            "identity_context": identity_context,
        })["session_id"]
        live = LiveSession(sid, principal_id, tenant_id, mode, language, 16000, channel)
        self.live[sid] = live
        if self.state:
            self.state.event(tenant_id, "session", sid, "session.opened", {"principal_id": principal_id, "channel": channel.value, "mode": mode})
        return live

    def render_disclosure(self, session_id: str, *, text: str = DEFAULT_CALL_DISCLOSURE) -> tuple[bytes, dict]:
        live = self.live.get(session_id)
        if live is None:
            raise KeyError("unknown live session")
        session = self.core.sessions.get(session_id)
        if session is None:
            raise KeyError("unknown live session")
        pcm = b"".join(session.speak_disclosure(text))
        audio_sha256 = hashlib.sha256(pcm).hexdigest()
        pending = {
            "tenant_id": live.tenant_id,
            "principal_id": live.principal_id,
            "session_id": session_id,
            "channel": live.channel.value,
            "text": text,
            "audio_sha256": audio_sha256,
        }
        self.pending_disclosures[session_id] = pending
        return pcm, pending

    def confirm_disclosure(self, session_id: str, delivery_evidence: str) -> dict:
        pending = self.pending_disclosures.get(session_id)
        if pending is None:
            raise ValueError("no rendered disclosure pending for session")
        if self.disclosure_verifier is None:
            raise PermissionError("trusted disclosure verifier is not configured")
        ev = self.disclosure_verifier.verify_delivery(delivery_evidence, **pending)
        body = ev.unsigned_body() | {"signature": ev.signature}
        self.core.sessions[session_id].disclose(body, audio_sha256=pending["audio_sha256"])
        self.pending_disclosures.pop(session_id, None)
        if self.state:
            self.state.event(pending["tenant_id"], "session", session_id, "disclosure.delivered", body)
        return {"disclosed": True, "disclosure_evidence_hash": ev.evidence_hash(), "audio_sha256": pending["audio_sha256"]}

    def erase_voice(self, principal_id: str, *, tenant_id: str = "default") -> dict:
        self.get_principal(principal_id, tenant_id=tenant_id)
        result = self.core.erase(principal_id)
        if self.state:
            asset_id = result["asset_id"]
            consent = self.core.enrollment.consent_for(asset_id)
            rec = self.core.registry._by_hash.get(consent.voice_model_hash)
            cert = rec.death_certificate if rec else None
            lifecycle = {
                "status": "erased",
                "erased_at": rec.erased_at if rec else None,
                "death_certificate": (cert.body() | {"owner_signature": cert.owner_signature}) if cert else None,
            }
            self.state.put_voice_lifecycle(tenant_id, principal_id, lifecycle)
            self.state.event(tenant_id, "principal", principal_id, "voice.erased", result)
        self.live = {sid: live for sid, live in self.live.items() if live.principal_id != principal_id}
        return result

    async def is_speech(self, pcm16: bytes) -> bool:
        return await self.vad.is_speech(pcm16, sample_rate=16000)

    async def transcribe(self, pcm16: bytes, *, language: str | None = None) -> tuple[str, dict]:
        transcript, undertone = await asyncio.gather(
            self.asr.transcribe(pcm16, sample_rate=16000, language=language),
            self.input_undertone.analyze(pcm16, sample_rate=16000),
        )
        return transcript.text, undertone

    async def speak_stream(self, session_id: str, text: str, *, prosody: dict | None = None, agency: dict | AgencyContext | None = None):
        session = self.core.sessions.get(session_id)
        if session is None:
            raise KeyError("unknown live session")
        q: queue.Queue = queue.Queue(maxsize=64)
        sentinel = object()

        def worker():
            try:
                for chunk in session.speak(text, prosody, agency):
                    q.put(chunk)
            except BaseException as exc:
                q.put(exc)
            finally:
                q.put(sentinel)

        threading.Thread(target=worker, daemon=True).start()
        while True:
            item = await asyncio.to_thread(q.get)
            if item is sentinel:
                break
            if isinstance(item, BaseException):
                raise item
            yield item

    def observe_undertone(self, session_id: str, obj: dict) -> dict:
        self.core.sessions[session_id].observe_incoming_undertone(
            UndertoneObservation.from_body(obj, analyzer=str(obj.get("analyzer", "media-input")))
        )
        return self.core.sessions[session_id].agency_report()

    def interrupt(self, session_id: str) -> None:
        session = self.core.sessions.get(session_id)
        if session:
            session.barge_in()

    def refusal(self, session_id: str) -> dict:
        return self.core.record_recipient_refusal(session_id)

    def attest(self, session_id: str, nonce: str) -> dict:
        return self.core.attest_session(session_id, {"challenge_nonce": nonce})

    def close_live(self, session_id: str) -> dict:
        live = self.live.pop(session_id, None)
        self.pending_disclosures.pop(session_id, None)
        manifest = self.core.close_session(session_id)
        if live and self.state:
            self.state.event(live.tenant_id, "session", session_id, "session.closed", manifest)
        return manifest
