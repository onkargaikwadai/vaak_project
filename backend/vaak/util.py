"""vaak.util — canonical serialization and hashing helpers."""
from __future__ import annotations

import hashlib
import json


def canon(obj: object) -> bytes:
    """Deterministic canonical JSON bytes (stable across runs and hosts)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def merkle_root(leaves: list[str]) -> str:
    """Merkle root over hex leaf hashes; deterministic, duplicate-last on odd."""
    if not leaves:
        return sha256_hex(b"")
    level = [bytes.fromhex(x) for x in leaves]
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        level = [
            hashlib.sha256(level[i] + level[i + 1]).digest()
            for i in range(0, len(level), 2)
        ]
    return level[0].hex()


def merkle_proof(leaves: list[str], index: int) -> list[tuple[str, str]]:
    """Inclusion proof for leaves[index]: list of (side, sibling_hex)."""
    if not leaves or not (0 <= index < len(leaves)):
        raise ValueError("bad proof index")
    proof: list[tuple[str, str]] = []
    level = [bytes.fromhex(x) for x in leaves]
    i = index
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        sib = i ^ 1
        proof.append(("L" if sib < i else "R", level[sib].hex()))
        level = [
            hashlib.sha256(level[j] + level[j + 1]).digest()
            for j in range(0, len(level), 2)
        ]
        i //= 2
    return proof


def merkle_verify(leaf: str, proof: list[tuple[str, str]], root: str) -> bool:
    """Verify inclusion offline: needs only the leaf, proof, and anchored root."""
    h = bytes.fromhex(leaf)
    for side, sib in proof:
        s = bytes.fromhex(sib)
        h = hashlib.sha256((s + h) if side == "L" else (h + s)).digest()
    return h.hex() == root
