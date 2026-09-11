"""vaak.provenance — session manifests, hash chain, Chrysalis anchor interface.

Every session (conversation, call leg, broadcast) emits a manifest:
who spoke (credential), under which envelope version/hash, on which
channel, how many frames, and the running content digest. Manifests are
hash-chained per twin and periodically anchored (Merkle root) — in
production, to Chrysalis via the standard anchoring path; here, to an
in-memory log whose checkpoints are signed by a dedicated reference anchor
authority. Third-party verification recomputes the manifest hash, checks
Merkle inclusion, and verifies the checkpoint against an independently
pinned trust root.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .util import canon, sha256_hex, merkle_root, merkle_proof, merkle_verify

GENESIS = "0" * 64


@dataclass(frozen=True)
class SessionManifest:
    twin_id: str
    session_id: str
    credential_id: str
    voice_model_hash: str
    envelope_version: int
    envelope_hash: str
    verb: str
    channel: str
    register: str
    presence_checked: bool
    frames_emitted: int
    content_digest: str  # sha256 over all emitted (watermarked) audio
    disclosed: bool      # call-open self-identification actually happened
    last_credential_valid_at: float
    agency_policy_hash: str
    agency_event_count: int
    agency_events_digest: str
    evidence_hash: str
    termination_reason: str | None
    closed_at: float
    prev_hash: str
    identity_class: str = "developer_agent"
    principal_id: str | None = None
    identity_context_hash: str | None = None
    rights_grant_hash: str | None = None
    production_id: str | None = None
    performance_version: str | None = None
    conditioning_hash: str | None = None
    model_plan_hash: str | None = None
    language: str | None = None
    usage_class: str | None = None
    territory: str | None = None
    tenant_id: str | None = None
    presence_evidence_hash: str | None = None
    disclosure_evidence_hash: str | None = None
    disclosure_audio_sha256: str | None = None
    model_provider_id: str | None = None
    soft_binding_count: int = 0
    soft_binding_refs_digest: str | None = None

    def body(self) -> dict:
        return {
            "twin_id": self.twin_id,
            "session_id": self.session_id,
            "credential_id": self.credential_id,
            "voice_model_hash": self.voice_model_hash,
            "envelope_version": self.envelope_version,
            "envelope_hash": self.envelope_hash,
            "verb": self.verb,
            "channel": self.channel,
            "register": self.register,
            "presence_checked": self.presence_checked,
            "frames_emitted": self.frames_emitted,
            "content_digest": self.content_digest,
            "disclosed": self.disclosed,
            "last_credential_valid_at": self.last_credential_valid_at,
            "agency_policy_hash": self.agency_policy_hash,
            "agency_event_count": self.agency_event_count,
            "agency_events_digest": self.agency_events_digest,
            "evidence_hash": self.evidence_hash,
            "termination_reason": self.termination_reason,
            "closed_at": self.closed_at,
            "prev_hash": self.prev_hash,
            "identity_class": self.identity_class,
            "principal_id": self.principal_id,
            "identity_context_hash": self.identity_context_hash,
            "rights_grant_hash": self.rights_grant_hash,
            "production_id": self.production_id,
            "performance_version": self.performance_version,
            "conditioning_hash": self.conditioning_hash,
            "model_plan_hash": self.model_plan_hash,
            "language": self.language,
            "usage_class": self.usage_class,
            "territory": self.territory,
            "tenant_id": self.tenant_id,
            "presence_evidence_hash": self.presence_evidence_hash,
            "disclosure_evidence_hash": self.disclosure_evidence_hash,
            "disclosure_audio_sha256": self.disclosure_audio_sha256,
            "model_provider_id": self.model_provider_id,
            "soft_binding_count": self.soft_binding_count,
            "soft_binding_refs_digest": self.soft_binding_refs_digest,
        }

    def manifest_hash(self) -> str:
        return sha256_hex(canon(self.body()))


@dataclass
class Anchor:
    root: str
    manifest_hashes: list[str]
    anchored_at: float
    statement: dict
    authority_signature: str
    external_ref: str | None = None


class AnchorAuthority:
    """Reference trust root for Chrysalis anchoring.

    Production verification pins the public Chrysalis trust root/finality
    proof. The reference build models that trust boundary with a dedicated
    Ed25519 anchor authority whose public key is distributed out of band.
    """

    def __init__(self) -> None:
        self._key = Ed25519PrivateKey.generate()

    @property
    def public_key_pem(self) -> str:
        return (
            self._key.public_key()
            .public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode()
        )

    def sign(self, statement: dict) -> str:
        return self._key.sign(canon(statement)).hex()


@dataclass(frozen=True)
class VerificationTrustBundle:
    """Verification keys that must be obtained independently of a receipt.

    Production maps these references to Chrysalis/Plane-3 trust roots.
    The reference build exposes the exact public keys for contract testing.
    """

    anchor_public_key_pem: str
    issuer_public_key_pem: str
    owner_public_key_pem: str

    @classmethod
    def from_value(cls, value: "VerificationTrustBundle | dict") -> "VerificationTrustBundle":
        if isinstance(value, cls):
            return value
        return cls(
            anchor_public_key_pem=value["anchor_public_key_pem"],
            issuer_public_key_pem=value["issuer_public_key_pem"],
            owner_public_key_pem=value["owner_public_key_pem"],
        )


@dataclass(frozen=True)
class UtteranceReceipt:
    """A portable, offline-verifiable proof that one specific session was
    authorized and anchored. Verification needs the receipt plus an
    independently pinned anchor, issuer, and owner trust bundle; it does not
    require the reference detector service for exact-content proof."""

    manifest: dict           # full manifest body (self-describing)
    manifest_hash: str
    proof: list              # [(side, sibling_hex), ...]
    root: str
    anchored_at: float
    anchor_statement: dict
    anchor_signature: str
    authorization_evidence: dict

    @staticmethod
    def verify(
        receipt: "UtteranceReceipt",
        trust: VerificationTrustBundle | dict,
    ) -> bool:
        """Verify media authorization against independently pinned trust.

        A receipt may carry public keys as evidence, but those keys are not
        self-authenticating.  Full verification requires an independently
        obtained anchor key, credential-issuer key, and owner identity key.
        """
        trust = VerificationTrustBundle.from_value(trust)
        recomputed = sha256_hex(canon(receipt.manifest))
        if recomputed != receipt.manifest_hash:
            return False
        evidence = receipt.authorization_evidence
        if sha256_hex(canon(evidence)) != receipt.manifest.get("evidence_hash"):
            return False
        if not UtteranceReceipt._verify_authorization(receipt.manifest, evidence, trust):
            return False
        if not merkle_verify(receipt.manifest_hash, receipt.proof, receipt.root):
            return False
        statement = receipt.anchor_statement
        if statement.get("type") != "vaak-chrysalis-anchor-reference-v1":
            return False
        if statement.get("root") != receipt.root:
            return False
        if statement.get("anchored_at") != receipt.anchored_at:
            return False
        try:
            pub: Ed25519PublicKey = serialization.load_pem_public_key(
                trust.anchor_public_key_pem.encode()
            )
            pub.verify(bytes.fromhex(receipt.anchor_signature), canon(statement))
        except (InvalidSignature, ValueError, TypeError):
            return False
        return True


    @staticmethod
    def _verify_authorization(
        manifest: dict,
        evidence: dict,
        trust: VerificationTrustBundle,
    ) -> bool:
        if evidence.get("type") != "vaak-authorization-evidence-v1":
            return False
        credential = dict(evidence.get("credential") or {})
        envelope = dict(evidence.get("envelope") or {})
        issuer_pem = evidence.get("issuer_public_key_pem")
        owner_pem = evidence.get("owner_public_key_pem")
        if issuer_pem != trust.issuer_public_key_pem:
            return False
        if owner_pem != trust.owner_public_key_pem:
            return False
        cred_sig = credential.pop("issuer_signature", None)
        env_sig = envelope.pop("owner_signature", None)
        if not all((issuer_pem, owner_pem, cred_sig, env_sig)):
            return False
        try:
            issuer: Ed25519PublicKey = serialization.load_pem_public_key(
                trust.issuer_public_key_pem.encode()
            )
            issuer.verify(bytes.fromhex(cred_sig), canon(credential))
            owner: Ed25519PublicKey = serialization.load_pem_public_key(
                trust.owner_public_key_pem.encode()
            )
            owner.verify(bytes.fromhex(env_sig), canon(envelope))
        except (InvalidSignature, ValueError, TypeError, AttributeError):
            return False

        if credential.get("credential_id") != manifest.get("credential_id"):
            return False
        if credential.get("twin_id") != manifest.get("twin_id"):
            return False
        if credential.get("voice_model_hash") != manifest.get("voice_model_hash"):
            return False
        valid_at = manifest.get("last_credential_valid_at")
        if not isinstance(valid_at, (int, float)):
            return False
        if valid_at < credential.get("issued_at", float("inf")):
            return False
        if valid_at >= credential.get("expires_at", float("-inf")):
            return False

        status = dict(evidence.get("credential_status") or {})
        status_statement = dict(status.get("statement") or {})
        status_sig = status.get("issuer_signature")
        if status_statement.get("type") != "vaak-credential-status-reference-v1":
            return False
        if status_statement.get("credential_id") != manifest.get("credential_id"):
            return False
        if status_statement.get("status") != "valid":
            return False
        if status_statement.get("checked_at") != valid_at:
            return False
        if status_statement.get("expires_at") != credential.get("expires_at"):
            return False
        try:
            issuer.verify(bytes.fromhex(status_sig), canon(status_statement))
        except (InvalidSignature, ValueError, TypeError, AttributeError):
            return False

        if sha256_hex(canon(envelope)) != manifest.get("envelope_hash"):
            return False
        if envelope.get("twin_id") != manifest.get("twin_id"):
            return False
        if envelope.get("version") != manifest.get("envelope_version"):
            return False
        if manifest.get("verb") not in envelope.get("granted", []):
            return False
        if manifest.get("register") not in envelope.get("registers", []):
            return False
        if (
            manifest.get("verb") in envelope.get("high_consequence", [])
            and not manifest.get("presence_checked")
        ):
            return False
        if (
            manifest.get("channel") == "call"
            and envelope.get("disclose_on_calls", True)
            and not manifest.get("disclosed")
        ):
            return False

        agency_policy = envelope.get("agency_policy")
        if not isinstance(agency_policy, dict):
            return False
        if sha256_hex(canon(agency_policy)) != manifest.get("agency_policy_hash"):
            return False
        identity_context = evidence.get("identity_context") or {}
        if manifest.get("identity_context_hash"):
            if sha256_hex(canon(identity_context)) != manifest.get("identity_context_hash"):
                return False
        if manifest.get("identity_class") == "studio_character":
            rights = dict(identity_context.get("rights_grant") or {})
            rights_hash = identity_context.get("rights_grant_hash")
            if not rights or rights_hash != manifest.get("rights_grant_hash"):
                return False
            if sha256_hex(canon(rights)) != rights_hash:
                return False
            unsigned = dict(rights)
            holder_sig = unsigned.pop("rights_holder_signature", None)
            unsigned.pop("performer_signature", None)
            unsigned.pop("revoked_at", None)
            unsigned.pop("revocation_reason", None)
            if not holder_sig or rights.get("rights_holder_public_key_pem") != trust.owner_public_key_pem:
                return False
            try:
                owner.verify(bytes.fromhex(holder_sig), canon(unsigned))
            except (InvalidSignature, ValueError, TypeError, AttributeError):
                return False
            if rights.get("revoked_at") is not None:
                return False
            scope = rights.get("scope") or {}
            if manifest.get("production_id") not in scope.get("production_ids", []):
                return False
            if manifest.get("language") not in scope.get("languages", []):
                return False
            if manifest.get("channel") not in scope.get("channels", []):
                return False
            if manifest.get("usage_class") not in scope.get("usage_classes", []):
                return False
            territories = scope.get("territories", [])
            if manifest.get("territory") not in territories and "worldwide" not in territories:
                return False
            if manifest.get("performance_version") != rights.get("voice_version"):
                return False
            if rights.get("performer_id"):
                performer_pem = rights.get("performer_public_key_pem")
                performer_sig = rights.get("performer_signature")
                if not performer_pem or not performer_sig:
                    return False
                try:
                    performer: Ed25519PublicKey = serialization.load_pem_public_key(performer_pem.encode())
                    performer.verify(bytes.fromhex(performer_sig), canon(unsigned))
                except (InvalidSignature, ValueError, TypeError, AttributeError):
                    return False

        agency_events = evidence.get("agency_events")
        if not isinstance(agency_events, list):
            return False
        if len(agency_events) != manifest.get("agency_event_count"):
            return False
        if sha256_hex(canon(agency_events)) != manifest.get("agency_events_digest"):
            return False
        return True


class ProvenanceLog:
    def __init__(self, authority: AnchorAuthority | None = None, publisher=None, *, auto_publish: bool = False) -> None:
        self._chains: dict[str, list[SessionManifest]] = {}
        self._unanchored: list[str] = []
        self._evidence: dict[str, dict] = {}
        self.anchors: list[Anchor] = []
        self._authority = authority or AnchorAuthority()
        self._publisher = publisher
        self._auto_publish = bool(auto_publish)

    @property
    def anchor_public_key_pem(self) -> str:
        return self._authority.public_key_pem

    def head(self, twin_id: str) -> str:
        chain = self._chains.get(twin_id)
        return chain[-1].manifest_hash() if chain else GENESIS

    def append(self, manifest: SessionManifest, authorization_evidence: dict) -> str:
        expected_prev = self.head(manifest.twin_id)
        if manifest.prev_hash != expected_prev:
            raise ValueError("manifest chain break")
        self._chains.setdefault(manifest.twin_id, []).append(manifest)
        if sha256_hex(canon(authorization_evidence)) != manifest.evidence_hash:
            raise ValueError("authorization evidence hash mismatch")
        h = manifest.manifest_hash()
        self._evidence[h] = authorization_evidence
        self._unanchored.append(h)
        if self._auto_publish and self._publisher is not None:
            self.anchor()
        return h

    def append_record(self, leaf_hash: str) -> None:
        """Enqueue a non-manifest lifecycle record (consent / death
        certificate) for inclusion in the next anchor batch."""
        self._unanchored.append(leaf_hash)
        if self._auto_publish and self._publisher is not None:
            self.anchor()

    def anchor(self) -> Anchor:
        """Batch-anchor pending manifests and lifecycle records.

        Production submits the root to Chrysalis. The reference build signs
        the anchor statement with a dedicated trust-root key so an offline
        verifier can distinguish a real checkpoint from a self-asserted root.
        """
        batch = list(self._unanchored)
        self._unanchored.clear()
        anchored_at = time.time()
        root = merkle_root(batch)
        statement = {
            "type": "vaak-chrysalis-anchor-reference-v1",
            "root": root,
            "leaf_count": len(batch),
            "anchored_at": anchored_at,
        }
        signature = self._authority.sign(statement)
        external_ref = self._publisher.publish(statement, signature) if self._publisher is not None else None
        a = Anchor(
            root=root,
            manifest_hashes=batch,
            anchored_at=anchored_at,
            statement=statement,
            authority_signature=signature,
            external_ref=external_ref,
        )
        self.anchors.append(a)
        return a

    # -- third-party verification -----------------------------------------
    def verify_chain(self, twin_id: str) -> bool:
        prev = GENESIS
        for m in self._chains.get(twin_id, []):
            if m.prev_hash != prev:
                return False
            prev = m.manifest_hash()
        return True

    def verify_inclusion(self, manifest_hash: str) -> bool:
        return any(manifest_hash in a.manifest_hashes for a in self.anchors)

    def receipt(self, manifest_hash: str) -> UtteranceReceipt:
        """Issue a portable inclusion receipt for an anchored manifest."""
        for a in self.anchors:
            if manifest_hash in a.manifest_hashes:
                idx = a.manifest_hashes.index(manifest_hash)
                manifest = self._by_hash(manifest_hash)
                return UtteranceReceipt(
                    manifest=manifest.body(),
                    manifest_hash=manifest_hash,
                    proof=merkle_proof(a.manifest_hashes, idx),
                    root=a.root,
                    anchored_at=a.anchored_at,
                    anchor_statement=a.statement,
                    anchor_signature=a.authority_signature,
                    authorization_evidence=self._evidence[manifest_hash],
                )
        raise ValueError("manifest not anchored")

    def _by_hash(self, manifest_hash: str) -> SessionManifest:
        for chain in self._chains.values():
            for m in chain:
                if m.manifest_hash() == manifest_hash:
                    return m
        raise ValueError("unknown manifest")

    def by_session(self, session_id: str) -> SessionManifest | None:
        for chain in self._chains.values():
            for m in chain:
                if m.session_id == session_id:
                    return m
        return None
