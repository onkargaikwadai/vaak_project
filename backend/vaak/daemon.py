"""vaakd — the Vaak daemon: HTTP owner API over the governance core.

Legacy core operations use a subject identifier internally; the v2.0 product API exposes tenant-scoped principals.

Endpoints (JSON unless noted):
  POST /twins/{id}/enroll/open           -> {session_id, challenge}
  POST /twins/{id}/enroll/complete       -> {asset_id, voice_model_hash, consent_hash}
  POST /twins/{id}/envelope              -> {version, envelope_hash}
  POST /twins/{id}/sessions              -> {session_id}
  POST /sessions/{id}/disclose           -> {disclosed: true}
  POST /sessions/{id}/speak              -> audio/L16 body (watermarked PCM)
  POST /sessions/{id}/barge-in           -> {stopped: true}
  POST /sessions/{id}/close              -> manifest JSON
  POST /twins/{id}/erase                 -> {shredded: true, ...}
  POST /verify/audio                     -> {present, confidence}
  POST /anchor                           -> {root, count}

Stdlib HTTP server; single-process reference. Production fronting is
Corridor-terminated and Crucible-enforced; this daemon is the
authoritative policy core those layers replicate.
"""
from __future__ import annotations

import base64
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .identity import Intermediate, SubjectKeypair, CredentialError
from .enrollment import EnrollmentService, EnrollmentError
from .envelope import (
    ChannelClass,
    EnvelopeEnforcer,
    EnvelopeError,
    VoiceVerb,
    sign_envelope,
)
from .provenance import ProvenanceLog
from .agency import (
    AgencyIntegrityError, AgencyPolicy, ReferenceUndertoneAnalyzer,
    UndertoneObservation, AgencyClassifier,
)
from .session import VoiceSession, SessionError
from .synthesis import StubBackend, SynthesisBackend
from .vault import VoiceVault, VaultError
from .registry import StatusRegistry, DeathCertificate
from . import watermark


class VaakCore:
    """All daemon state; the HTTP layer is a thin shell over this."""

    def __init__(self, *, backend: SynthesisBackend | None = None, undertone_analyzer=None, agency_classifier: AgencyClassifier | None = None, intermediate: Intermediate | None = None, owner_key_factory=None, log: ProvenanceLog | None = None, vault=None, soft_binder=None) -> None:
        self.vault = vault or VoiceVault()
        self.intermediate = intermediate or Intermediate()
        self.enrollment = EnrollmentService(self.vault)
        self.log = log or ProvenanceLog()
        self._owner_key_factory = owner_key_factory or (lambda principal_id: SubjectKeypair())
        self.backend = backend or StubBackend()
        self.undertone_analyzer = undertone_analyzer or ReferenceUndertoneAnalyzer()
        self.agency_classifier = agency_classifier
        self.soft_binder = soft_binder
        self.owners: dict[str, SubjectKeypair] = {}       # twin_id -> owner key
        self.envelopes: dict[str, EnvelopeEnforcer] = {}  # twin_id -> enforcer
        self.assets: dict[str, str] = {}                  # twin_id -> asset_id
        self.reference_text: dict[str, str] = {}           # asset_id -> enrollment transcript
        self.sessions: dict[str, VoiceSession] = {}
        self.registry = StatusRegistry()

    def owner_for(self, twin_id: str):
        return self.owners.setdefault(twin_id, self._owner_key_factory(twin_id))

    # ---- operations ------------------------------------------------------
    def enroll_open(self, twin_id: str) -> dict:
        s = self.enrollment.open_session(twin_id)
        return {"session_id": s.session_id, "challenge": s.challenge}

    def enroll_complete(self, twin_id: str, body: dict) -> dict:
        model = base64.b64decode(body["voice_model_b64"])
        asset, consent = self.enrollment.complete(
            body["session_id"], model, body["spoken_challenge"],
            self.owner_for(twin_id),
            languages=tuple(body.get("languages", ["en"])),
            enrollment_evidence_hash=body.get("enrollment_evidence_hash"),
        )
        self.assets[twin_id] = asset.asset_id
        self.reference_text[asset.asset_id] = str(body.get("spoken_challenge") or "")
        self.registry.register(twin_id, asset.asset_id,
                               asset.voice_model_hash, consent.enrolled_at)
        self.log.append_record(consent.record_hash())  # birth record anchored
        return {
            "asset_id": asset.asset_id,
            "voice_model_hash": asset.voice_model_hash,
            "consent_hash": consent.record_hash(),
        }

    def set_envelope(self, twin_id: str, body: dict) -> dict:
        from .envelope import Register
        env = sign_envelope(
            twin_id=twin_id,
            version=int(body["version"]),
            granted={VoiceVerb(v) for v in body.get("granted", [])},
            high_consequence={VoiceVerb(v) for v in body.get("high_consequence", [])},
            registers={Register(r) for r in body.get("registers", ["private"])},
            disclose_on_calls=bool(body.get("disclose_on_calls", True)),
            agency_policy=AgencyPolicy.from_body(body.get("agency_policy")),
            owner=self.owner_for(twin_id),
        )
        self.envelopes[twin_id] = EnvelopeEnforcer(env)
        return {"version": env.version, "envelope_hash": env.envelope_hash()}

    def open_session(self, twin_id: str, body: dict) -> dict:
        enforcer = self.envelopes.get(twin_id)
        if enforcer is None:
            raise EnvelopeError("no envelope set for principal")
        asset_id = self.assets.get(twin_id)
        if asset_id is None:
            raise EnrollmentError("principal has no enrolled voice")
        if self.vault.is_shredded(asset_id):
            raise VaultError("voice erased; new sessions are prohibited")
        from .envelope import Register
        auth = enforcer.authorize(
            VoiceVerb(body["verb"]),
            ChannelClass(body["channel"]),
            register=Register(body.get("register", "private")),
            presence_checked=bool(body.get("presence_checked", False)),
        )
        owner = self.owner_for(twin_id)
        cred_subject = SubjectKeypair()
        consent = self.enrollment.consent_for(asset_id)
        credential = self.intermediate.issue(
            twin_id, consent.voice_model_hash, cred_subject.public_pem
        )
        session = VoiceSession(
            twin_id=twin_id,
            credential=credential,
            authorization=auth,
            intermediate=self.intermediate,
            vault=self.vault,
            enrollment=self.enrollment,
            backend=self.backend,
            log=self.log,
            asset_id=asset_id,
            owner_public_key_pem=owner.public_pem,
            subject=cred_subject,
            language=body.get("language", "en"),
            undertone_analyzer=self.undertone_analyzer,
            agency_classifier=self.agency_classifier,
            reference_text=self.reference_text.get(asset_id),
            identity_context=body.get("identity_context"),
            soft_binder=self.soft_binder,
        )
        del owner  # owner key not needed past envelope/consent signing
        self.sessions[session.session_id] = session
        return {"session_id": session.session_id}

    def speak(self, session_id: str, body: dict) -> bytes:
        session = self.sessions.get(session_id)
        if session is None:
            raise SessionError("unknown session")
        return b"".join(session.speak(
            body["text"], body.get("prosody"), body.get("agency")
        ))

    def observe_incoming_undertone(self, session_id: str, body: dict) -> dict:
        session = self.sessions.get(session_id)
        if session is None:
            raise SessionError("unknown session")
        session.observe_incoming_undertone(body)
        return session.agency_report()

    def record_recipient_refusal(self, session_id: str) -> dict:
        session = self.sessions.get(session_id)
        if session is None:
            raise SessionError("unknown session")
        session.record_recipient_refusal()
        return session.agency_report()

    def record_material_change(self, session_id: str, evidence_ref: str) -> dict:
        """Trusted in-process policy-plane hook; intentionally no public route."""
        session = self.sessions.get(session_id)
        if session is None:
            raise SessionError("unknown session")
        session.record_material_change(evidence_ref)
        return session.agency_report()

    def agency_report(self, session_id: str) -> dict:
        session = self.sessions.get(session_id)
        if session is None:
            raise SessionError("unknown session")
        return session.agency_report()

    def close_session(self, session_id: str) -> dict:
        session = self.sessions.pop(session_id, None)
        if session is None:
            raise SessionError("unknown session")
        m = session.close()
        return m.body() | {"manifest_hash": m.manifest_hash()}

    def erase(self, twin_id: str) -> dict:
        import time as _t
        asset_id = self.assets.get(twin_id)
        if asset_id is None:
            raise VaultError("principal has no enrolled voice")
        if self.vault.is_shredded(asset_id):
            raise VaultError("voice already erased")
        consent = self.enrollment.consent_for(asset_id)
        erased_at = _t.time()

        # Authorization death precedes data destruction.  If a failure occurs
        # mid-operation, the conservative state is "cannot speak", never
        # "shredded model but still live credential".
        revoked_ids = self.intermediate.revoke_for_voice_model(
            consent.voice_model_hash
        )
        active = [
            (sid, session)
            for sid, session in list(self.sessions.items())
            if session.twin_id == twin_id
        ]
        terminated_ids: list[str] = []
        terminated_manifests: list[str] = []
        for sid, session in active:
            manifest = session.terminate("voice_erased")
            terminated_ids.append(sid)
            terminated_manifests.append(manifest.manifest_hash())
            self.sessions.pop(sid, None)

        self.vault.crypto_shred(asset_id)
        cert_body = {
            "twin_id": twin_id, "asset_id": asset_id,
            "voice_model_hash": consent.voice_model_hash,
            "erased_at": erased_at,
            "revoked_credential_ids": tuple(revoked_ids),
            "terminated_session_ids": tuple(sorted(terminated_ids)),
        }
        from .util import canon as _c
        cert = DeathCertificate(
            owner_signature=self.owner_for(twin_id).sign(_c(cert_body)),
            **cert_body,
        )
        self.registry.mark_erased(consent.voice_model_hash, cert)
        self.log.append_record(cert.certificate_hash())  # death cert anchored
        # Detection survives by construction:
        dk = self.vault.detection_key(consent.voice_model_hash)
        return {
            "shredded": True,
            "asset_id": asset_id,
            "revoked_credential_ids": revoked_ids,
            "terminated_session_ids": sorted(terminated_ids),
            "terminated_manifest_hashes": terminated_manifests,
            "detection_still_available": bool(dk),
            "death_certificate_hash": cert.certificate_hash(),
        }

    def verify_audio(self, body: dict) -> dict:
        pcm = base64.b64decode(body["audio_b64"])
        vm_hash = body["voice_model_hash"]
        dk = self.vault.detection_key(vm_hash)
        r = watermark.detect(pcm, dk)
        return {
            "present": r.present,
            "confidence": round(r.confidence, 4),
            "frames_checked": r.frames_checked,
        } | self.registry.status(vm_hash, r.present)

    def resolve_clip(self, pcm: bytes) -> dict:
        """Self-attesting audio: beacon -> session -> manifest -> status
        (+ portable receipt when anchored). No key required to resolve;
        keyed detection then confirms authenticity."""
        beacon = watermark.extract_beacon(pcm)
        if beacon is None:
            return {"resolved": False, "status": "no_beacon"}
        for sid_manifest in self._all_manifests():
            expected = __import__("hashlib").sha256(
                f"{sid_manifest.twin_id}|{sid_manifest.session_id}".encode()
            ).digest()[: watermark.BEACON_BYTES]
            if expected == beacon:
                m = sid_manifest
                dk = self.vault.detection_key(
                    self.enrollment.consent_for(
                        self.assets[m.twin_id]).voice_model_hash)
                det = watermark.detect(pcm, dk)
                out = {
                    "resolved": True,
                    "twin_id": m.twin_id,
                    "session_id": m.session_id,
                    "manifest_hash": m.manifest_hash(),
                    "authentic": det.present,
                    "confidence": round(det.confidence, 4),
                }
                if self.log.verify_inclusion(m.manifest_hash()):
                    rec = self.log.receipt(m.manifest_hash())
                    out["receipt"] = {
                        "manifest": rec.manifest,
                        "manifest_hash": rec.manifest_hash,
                        "proof": rec.proof,
                        "root": rec.root,
                        "anchored_at": rec.anchored_at,
                        "anchor_statement": rec.anchor_statement,
                        "anchor_signature": rec.anchor_signature,
                        "authorization_evidence": rec.authorization_evidence,
                    }
                    out["offline_exact_content_match"] = (
                        __import__("hashlib").sha256(pcm).hexdigest()
                        == m.content_digest
                    )
                return out
        return {"resolved": False, "status": "unknown_beacon"}

    def _all_manifests(self):
        for chain in self.log._chains.values():
            yield from chain

    def attest_session(self, session_id: str, body: dict) -> dict:
        session = self.sessions.get(session_id)
        if session is None:
            raise SessionError("unknown session")
        return session.attest(body["challenge_nonce"])

    def proof_trust_root(self) -> dict:
        return {
            "scheme": "Ed25519-reference-anchor",
            "public_key_pem": self.log.anchor_public_key_pem,
        }

    def verification_trust_bundle(self, twin_id: str) -> dict:
        owner = self.owner_for(twin_id)
        return {
            "scheme": "vaak-reference-trust-bundle-v1",
            "anchor_public_key_pem": self.log.anchor_public_key_pem,
            "issuer_public_key_pem": self.intermediate.public_key_pem,
            "owner_public_key_pem": owner.public_pem,
        }

    def anchor(self) -> dict:
        a = self.log.anchor()
        return {
            "root": a.root,
            "count": len(a.manifest_hashes),
            "anchored_at": a.anchored_at,
            "anchor_statement": a.statement,
            "anchor_signature": a.authority_signature,
        }


_ERRORS = (
    CredentialError, EnrollmentError, EnvelopeError, SessionError, AgencyIntegrityError,
    VaultError, watermark.WatermarkError, KeyError, ValueError,
)


def make_handler(core: VaakCore):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # quiet
            pass

        def _json(self, code: int, obj: dict) -> None:
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _audio(self, pcm: bytes) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "audio/L16;rate=16000")
            self.send_header("Content-Length", str(len(pcm)))
            self.end_headers()
            self.wfile.write(pcm)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw or b"{}")
                parts = [p for p in self.path.split("/") if p]
                match parts:
                    case ["twins", tid, "enroll", "open"]:
                        self._json(200, core.enroll_open(tid))
                    case ["twins", tid, "enroll", "complete"]:
                        self._json(200, core.enroll_complete(tid, body))
                    case ["twins", tid, "envelope"]:
                        self._json(200, core.set_envelope(tid, body))
                    case ["twins", tid, "sessions"]:
                        self._json(200, core.open_session(tid, body))
                    case ["twins", tid, "erase"]:
                        self._json(200, core.erase(tid))
                    case ["sessions", sid, "disclose"]:
                        core.sessions[sid].disclose()
                        self._json(200, {"disclosed": True})
                    case ["sessions", sid, "speak"]:
                        self._audio(core.speak(sid, body))
                    case ["sessions", sid, "attest"]:
                        self._json(200, core.attest_session(sid, body))
                    case ["sessions", sid, "undertone", "incoming"]:
                        self._json(200, core.observe_incoming_undertone(sid, body))
                    case ["sessions", sid, "recipient-refusal"]:
                        self._json(200, core.record_recipient_refusal(sid))
                    case ["sessions", sid, "agency-report"]:
                        self._json(200, core.agency_report(sid))
                    case ["resolve", "clip"]:
                        self._json(200, core.resolve_clip(
                            base64.b64decode(body["audio_b64"])))
                    case ["sessions", sid, "barge-in"]:
                        core.sessions[sid].barge_in()
                        self._json(200, {"stopped": True})
                    case ["sessions", sid, "close"]:
                        self._json(200, core.close_session(sid))
                    case ["verify", "audio"]:
                        self._json(200, core.verify_audio(body))
                    case ["proof", "trust-root"]:
                        self._json(200, core.proof_trust_root())
                    case ["twins", tid, "verification-trust-bundle"]:
                        self._json(200, core.verification_trust_bundle(tid))
                    case ["anchor"]:
                        self._json(200, core.anchor())
                    case _:
                        self._json(404, {"error": "no such endpoint"})
            except _ERRORS as e:
                self._json(403, {"error": f"{type(e).__name__}: {e}"})
            except Exception as e:  # noqa: BLE001 — reference daemon
                self._json(500, {"error": f"{type(e).__name__}: {e}"})

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8477) -> None:
    core = VaakCore()
    httpd = ThreadingHTTPServer((host, port), make_handler(core))
    print(f"vaakd listening on http://{host}:{port}")
    httpd.serve_forever()


if __name__ == "__main__":
    serve()
