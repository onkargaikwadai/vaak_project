"""vaak.identity — leaf credential model for voice assets.

Per the locked Parinita credential architecture: high-churn leaf credentials
are classical (Ed25519) minted at line rate by Plane 3 intermediates; the
post-quantum ML-DSA-87 chain lives at root/intermediate and is OUT OF SCOPE
for this daemon. This module models the leaf tier only. The intermediate
signer here is a stand-in for the nShield-backed issuance path.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from dataclasses import dataclass, field

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature

from .util import canon, sha256_hex


class CredentialError(Exception):
    pass


@dataclass(frozen=True)
class LeafCredential:
    """A short-lived voice credential bound to a twin and a voice model."""

    credential_id: str
    twin_id: str
    voice_model_hash: str  # binds credential to exactly one enrolled voice
    public_key_pem: str
    issued_at: float
    expires_at: float
    issuer_signature: str  # hex, over the canonical body, by the intermediate

    def body(self) -> dict:
        return {
            "credential_id": self.credential_id,
            "twin_id": self.twin_id,
            "voice_model_hash": self.voice_model_hash,
            "public_key_pem": self.public_key_pem,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
        }

    def is_expired(self, at: float | None = None) -> bool:
        now = time.time() if at is None else at
        return now >= self.expires_at


class Intermediate:
    """Leaf issuer. Production may delegate signatures to an HSM service."""

    def __init__(self, signer=None, state_path: str | None = None) -> None:
        self._key = None if signer is not None else Ed25519PrivateKey.generate()
        self._signer = signer
        self._state_path = state_path
        self._lock = threading.RLock()
        self.public_key_pem = signer.public_pem if signer is not None else (
            self._key.public_key()
            .public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode()
        )
        self._revoked: set[str] = set()
        self._issued: dict[str, LeafCredential] = {}
        if self._state_path:
            Path(self._state_path).parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self._state_path) as db:
                db.execute(
                    """CREATE TABLE IF NOT EXISTS leaf_credentials(
                        credential_id TEXT PRIMARY KEY,
                        voice_model_hash TEXT NOT NULL,
                        body_json TEXT NOT NULL,
                        revoked INTEGER NOT NULL DEFAULT 0
                    )"""
                )
            self._reload_state()


    def _connect(self):
        if not self._state_path:
            raise RuntimeError("credential state is not persistent")
        db = sqlite3.connect(self._state_path, timeout=5.0)
        db.row_factory = sqlite3.Row
        return db

    def _reload_state(self) -> None:
        if not self._state_path:
            return
        with self._connect() as db:
            rows = db.execute("SELECT body_json, revoked FROM leaf_credentials").fetchall()
        for row in rows:
            obj = json.loads(row["body_json"])
            cred = LeafCredential(**obj)
            self._issued[cred.credential_id] = cred
            if bool(row["revoked"]):
                self._revoked.add(cred.credential_id)

    def _persist(self, cred: LeafCredential, *, revoked: bool = False) -> None:
        if not self._state_path:
            return
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO leaf_credentials(credential_id,voice_model_hash,body_json,revoked) VALUES(?,?,?,?)",
                (cred.credential_id, cred.voice_model_hash, json.dumps(cred.body() | {"issuer_signature": cred.issuer_signature}, sort_keys=True), 1 if revoked else 0),
            )

    def _sign(self, data: bytes) -> str:
        if self._signer is not None:
            return self._signer.sign(data)
        return self._key.sign(data).hex()

    def issue(
        self,
        twin_id: str,
        voice_model_hash: str,
        subject_public_pem: str,
        ttl_seconds: float = 3600.0,
        at: float | None = None,
    ) -> LeafCredential:
        issued = time.time() if at is None else at
        cred_id = sha256_hex(f"{twin_id}|{voice_model_hash}|{issued}".encode())[:32]
        body = {
            "credential_id": cred_id,
            "twin_id": twin_id,
            "voice_model_hash": voice_model_hash,
            "public_key_pem": subject_public_pem,
            "issued_at": issued,
            "expires_at": issued + ttl_seconds,
        }
        sig = self._sign(canon(body))
        cred = LeafCredential(issuer_signature=sig, **body)
        self._issued[cred.credential_id] = cred
        self._persist(cred)
        return cred

    def revoke(self, credential_id: str) -> None:
        self._revoked.add(credential_id)
        cred = self._issued.get(credential_id)
        if cred is not None:
            self._persist(cred, revoked=True)

    def revoke_for_voice_model(self, voice_model_hash: str) -> list[str]:
        """Revoke every credential ever issued for one voice model."""
        ids = [
            cid for cid, cred in self._issued.items()
            if cred.voice_model_hash == voice_model_hash
        ]
        self._revoked.update(ids)
        for cid in ids:
            self._persist(self._issued[cid], revoked=True)
        return sorted(ids)

    def is_revoked(self, credential_id: str) -> bool:
        return credential_id in self._revoked

    def verify(self, cred: LeafCredential, at: float | None = None) -> None:
        """Raise CredentialError unless the credential is currently valid."""
        if self.is_revoked(cred.credential_id):
            raise CredentialError("credential revoked")
        if cred.is_expired(at):
            raise CredentialError("credential expired")
        pub: Ed25519PublicKey = serialization.load_pem_public_key(
            self.public_key_pem.encode()
        )
        try:
            pub.verify(bytes.fromhex(cred.issuer_signature), canon(cred.body()))
        except InvalidSignature as e:
            raise CredentialError("bad issuer signature") from e

    def status_proof(self, cred: LeafCredential, at: float | None = None) -> dict:
        """Return an issuer-signed proof that the credential was valid at T.

        Production replaces this reference proof with the authoritative
        Crucible/Chrysalis credential-status path.
        """
        checked_at = time.time() if at is None else at
        self.verify(cred, at=checked_at)
        statement = {
            "type": "vaak-credential-status-reference-v1",
            "credential_id": cred.credential_id,
            "status": "valid",
            "checked_at": checked_at,
            "expires_at": cred.expires_at,
        }
        return {
            "statement": statement,
            "issuer_signature": self._sign(canon(statement)),
        }


@dataclass
class SubjectKeypair:
    """The twin-held keypair a leaf credential certifies."""

    private_key: Ed25519PrivateKey = field(default_factory=Ed25519PrivateKey.generate)

    @property
    def public_pem(self) -> str:
        return (
            self.private_key.public_key()
            .public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode()
        )

    def sign(self, data: bytes) -> str:
        return self.private_key.sign(data).hex()
