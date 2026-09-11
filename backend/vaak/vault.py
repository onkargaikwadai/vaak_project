"""vaak.vault — encrypted-at-rest storage for voice model assets.

Uses the Vaak per-asset encryption pattern: per-asset DEK (AES-256-GCM), DEK
wrapped by a daemon KEK. Erasure is crypto-shred: destroy the wrapped DEK
and the ciphertext is permanently unrecoverable. The watermark DETECTION
key is derived (HKDF) from the voice model hash + a fabric detection secret,
NOT stored with the model — so detection of previously issued audio survives
erasure while reproduction does not.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

from .util import sha256_hex


class VaultError(Exception):
    pass


@dataclass
class StoredAsset:
    asset_id: str
    voice_model_hash: str  # hash of PLAINTEXT model — identity survives re-wrap
    nonce: bytes
    ciphertext: bytes
    wrapped_dek: bytes
    dek_nonce: bytes
    shredded: bool = False


class VoiceVault:
    def __init__(self, kek: bytes | None = None, detection_secret: bytes | None = None):
        self._kek = kek or AESGCM.generate_key(bit_length=256)
        self._detection_secret = detection_secret or os.urandom(32)
        self._assets: dict[str, StoredAsset] = {}

    # -- storage -----------------------------------------------------------
    def store(self, twin_id: str, model_bytes: bytes) -> StoredAsset:
        model_hash = sha256_hex(model_bytes)
        asset_id = sha256_hex(f"{twin_id}|{model_hash}".encode())[:32]
        dek = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(12)
        ct = AESGCM(dek).encrypt(nonce, model_bytes, asset_id.encode())
        dek_nonce = os.urandom(12)
        wrapped = AESGCM(self._kek).encrypt(dek_nonce, dek, asset_id.encode())
        asset = StoredAsset(asset_id, model_hash, nonce, ct, wrapped, dek_nonce)
        self._assets[asset_id] = asset
        return asset

    def load(self, asset_id: str) -> bytes:
        asset = self._assets.get(asset_id)
        if asset is None:
            raise VaultError("unknown asset")
        if asset.shredded:
            raise VaultError("asset crypto-shredded")
        dek = AESGCM(self._kek).decrypt(
            asset.dek_nonce, asset.wrapped_dek, asset_id.encode()
        )
        return AESGCM(dek).decrypt(asset.nonce, asset.ciphertext, asset_id.encode())

    def is_shredded(self, asset_id: str) -> bool:
        asset = self._assets.get(asset_id)
        if asset is None:
            raise VaultError("unknown asset")
        return asset.shredded

    # -- erasure -----------------------------------------------------------
    def crypto_shred(self, asset_id: str) -> None:
        asset = self._assets.get(asset_id)
        if asset is None:
            raise VaultError("unknown asset")
        asset.wrapped_dek = b""
        asset.dek_nonce = b""
        asset.shredded = True

    # -- detection key (survives shred by construction) --------------------
    def detection_key(self, voice_model_hash: str) -> bytes:
        """Derived, never stored with the model; usable after crypto-shred."""
        hk = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=bytes.fromhex(voice_model_hash),
            info=b"vaak-watermark-detection-v1",
        )
        return hk.derive(self._detection_secret)


class LocalAESKeyWrapper:
    """Development/test key wrapper with a stable 256-bit wrapping key."""
    def __init__(self, key: bytes):
        import hashlib
        self._key = hashlib.sha256(key).digest()

    def wrap(self, data: bytes, *, context: str) -> bytes:
        nonce = os.urandom(12)
        return nonce + AESGCM(self._key).encrypt(nonce, data, context.encode())

    def unwrap(self, wrapped: bytes, *, context: str) -> bytes:
        if len(wrapped) < 13:
            raise VaultError("invalid wrapped key")
        nonce, ct = wrapped[:12], wrapped[12:]
        return AESGCM(self._key).decrypt(nonce, ct, context.encode())


class DurableVoiceVault:
    """Durable encrypted voice-asset vault with external key wrapping.

    Ciphertext and wrapped DEKs are persisted. In production the wrapper is an
    HSM/key-service facade, so private wrapping keys never enter Vaak. Erasure
    deletes the wrapped DEK while retaining ciphertext and separately wrapped
    detection material for historical proof.
    """
    def __init__(self, path: str, wrapper):
        import sqlite3
        from pathlib import Path as _Path
        self.path = path
        self.wrapper = wrapper
        _Path(path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS voice_assets(
                    asset_id TEXT PRIMARY KEY,
                    voice_model_hash TEXT NOT NULL,
                    nonce BLOB NOT NULL,
                    ciphertext BLOB NOT NULL,
                    wrapped_dek BLOB NOT NULL,
                    shredded INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS voice_vault_meta(
                    name TEXT PRIMARY KEY,
                    value BLOB NOT NULL
                );
            """)
            row = db.execute("SELECT value FROM voice_vault_meta WHERE name='detection_secret'").fetchone()
            if row is None:
                wrapped = self.wrapper.wrap(os.urandom(32), context="vaak-detection-secret-v1")
                db.execute("INSERT INTO voice_vault_meta(name,value) VALUES('detection_secret',?)", (wrapped,))

    def _connect(self):
        import sqlite3
        db = sqlite3.connect(self.path, timeout=5.0)
        db.row_factory = sqlite3.Row
        return db

    def _get(self, asset_id: str) -> StoredAsset:
        with self._connect() as db:
            row = db.execute("SELECT * FROM voice_assets WHERE asset_id=?", (asset_id,)).fetchone()
        if row is None:
            raise VaultError("unknown asset")
        return StoredAsset(
            asset_id=str(row["asset_id"]),
            voice_model_hash=str(row["voice_model_hash"]),
            nonce=bytes(row["nonce"]),
            ciphertext=bytes(row["ciphertext"]),
            wrapped_dek=bytes(row["wrapped_dek"] or b""),
            dek_nonce=b"",
            shredded=bool(row["shredded"]),
        )

    def store(self, twin_id: str, model_bytes: bytes) -> StoredAsset:
        model_hash = sha256_hex(model_bytes)
        asset_id = sha256_hex(f"{twin_id}|{model_hash}".encode())[:32]
        dek = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(12)
        ct = AESGCM(dek).encrypt(nonce, model_bytes, asset_id.encode())
        wrapped = self.wrapper.wrap(dek, context=f"vaak-asset:{asset_id}")
        with self._connect() as db:
            old = db.execute("SELECT shredded FROM voice_assets WHERE asset_id=?", (asset_id,)).fetchone()
            if old and bool(old["shredded"]):
                raise VaultError("erased asset identity cannot be reactivated")
            db.execute(
                "INSERT OR REPLACE INTO voice_assets(asset_id,voice_model_hash,nonce,ciphertext,wrapped_dek,shredded) VALUES(?,?,?,?,?,0)",
                (asset_id, model_hash, nonce, ct, wrapped),
            )
        return StoredAsset(asset_id, model_hash, nonce, ct, wrapped, b"")

    def load(self, asset_id: str) -> bytes:
        asset = self._get(asset_id)
        if asset.shredded or not asset.wrapped_dek:
            raise VaultError("asset crypto-shredded")
        dek = self.wrapper.unwrap(asset.wrapped_dek, context=f"vaak-asset:{asset_id}")
        return AESGCM(dek).decrypt(asset.nonce, asset.ciphertext, asset_id.encode())

    def is_shredded(self, asset_id: str) -> bool:
        return self._get(asset_id).shredded

    def crypto_shred(self, asset_id: str) -> None:
        self._get(asset_id)
        with self._connect() as db:
            db.execute("UPDATE voice_assets SET wrapped_dek=x'', shredded=1 WHERE asset_id=?", (asset_id,))

    def detection_key(self, voice_model_hash: str) -> bytes:
        with self._connect() as db:
            row = db.execute("SELECT value FROM voice_vault_meta WHERE name='detection_secret'").fetchone()
        if row is None:
            raise VaultError("detection secret unavailable")
        secret = self.wrapper.unwrap(bytes(row[0]), context="vaak-detection-secret-v1")
        hk = HKDF(
            algorithm=hashes.SHA256(), length=32,
            salt=bytes.fromhex(voice_model_hash),
            info=b"vaak-watermark-detection-v1",
        )
        return hk.derive(secret)
