"""vaak.watermark — legacy test-only keyed watermark contract.

GA production never selects this module for audio egress. Production uses the
mandatory robust soft-binding service configured through the media binding
boundary. This deterministic implementation exists only for contract and
regression tests: (1) embedding is keyed per voice model, (2) detection uses
a derived key that survives crypto-shred, and (3) detection returns a bounded
signal/confidence result rather than inferring whether audio merely sounds
synthetic.

The test scheme uses keyed HMAC frame signatures in 16-bit PCM LSBs and is
intentionally easy to strip; it must not be configured as the GA binding
service.
"""
from __future__ import annotations

import hmac
import hashlib
from dataclasses import dataclass

FRAME_SAMPLES = 160  # 10ms at 16kHz
_BITS_PER_FRAME = 32  # keyed-tag LSB payload per frame (samples 0..31)
BEACON_BYTES = 16      # provenance beacon per frame (samples 32..159 LSBs)
_BEACON_BITS = BEACON_BYTES * 8


class WatermarkError(Exception):
    pass


def _frame_tag(key: bytes, frame_index: int, payload: bytes) -> bytes:
    msg = frame_index.to_bytes(8, "big") + payload
    return hmac.new(key, msg, hashlib.sha256).digest()[: _BITS_PER_FRAME // 8]


def embed(pcm16: bytes, key: bytes, frame_offset: int = 0,
          beacon: bytes | None = None) -> bytes:
    """Embed keyed tags (authenticity) and an optional provenance beacon
    (session resolution) into frame LSBs. The beacon is a pointer — recoverable
    without any key — that resolves a clip to its session manifest; the keyed
    tags provide the reference authenticity signal. frame_offset keeps chunked
    embedding consistent with whole-buffer detection."""
    if beacon is not None and len(beacon) != BEACON_BYTES:
        raise WatermarkError(f"beacon must be {BEACON_BYTES} bytes")
    if len(pcm16) % 2:
        raise WatermarkError("pcm16 length must be even")
    samples = bytearray(pcm16)
    n_frames = len(samples) // 2 // FRAME_SAMPLES
    for f in range(n_frames):
        base = f * FRAME_SAMPLES * 2
        # Payload = the frame's own upper bytes, so the tag binds to content.
        payload = bytes(samples[base + 1 : base + FRAME_SAMPLES * 2 : 2])
        tag = _frame_tag(key, frame_offset + f, payload)
        bits = int.from_bytes(tag, "big")
        for b in range(_BITS_PER_FRAME):
            idx = base + b * 2  # LSB of sample b's low byte
            bit = (bits >> (_BITS_PER_FRAME - 1 - b)) & 1
            samples[idx] = (samples[idx] & 0xFE) | bit
        if beacon is not None:
            bbits = int.from_bytes(beacon, "big")
            for b in range(_BEACON_BITS):
                idx = base + (_BITS_PER_FRAME + b) * 2
                bit = (bbits >> (_BEACON_BITS - 1 - b)) & 1
                samples[idx] = (samples[idx] & 0xFE) | bit
    return bytes(samples)


def extract_beacon(pcm16: bytes) -> bytes | None:
    """Keyless recovery of the provenance beacon (majority vote across
    frames). Returns None if frames disagree entirely (no beacon)."""
    if len(pcm16) % 2:
        raise WatermarkError("pcm16 length must be even")
    n_frames = len(pcm16) // 2 // FRAME_SAMPLES
    if n_frames == 0:
        return None
    counts: dict[bytes, int] = {}
    for f in range(n_frames):
        base = f * FRAME_SAMPLES * 2
        bits = 0
        for b in range(_BEACON_BITS):
            bits = (bits << 1) | (pcm16[base + (_BITS_PER_FRAME + b) * 2] & 1)
        cand = bits.to_bytes(BEACON_BYTES, "big")
        counts[cand] = counts.get(cand, 0) + 1
    best, votes = max(counts.items(), key=lambda kv: kv[1])
    return best if votes / n_frames >= 0.5 else None


@dataclass
class DetectionResult:
    present: bool
    confidence: float  # fraction of frames whose tags verify
    frames_checked: int


def detect(pcm16: bytes, detection_key: bytes) -> DetectionResult:
    """Check for the keyed signal. Detection key is DERIVED (vault.detection_key)."""
    if len(pcm16) % 2:
        raise WatermarkError("pcm16 length must be even")
    n_frames = len(pcm16) // 2 // FRAME_SAMPLES
    if n_frames == 0:
        return DetectionResult(False, 0.0, 0)
    ok = 0
    for f in range(n_frames):
        base = f * FRAME_SAMPLES * 2
        payload = bytes(pcm16[base + 1 : base + FRAME_SAMPLES * 2 : 2])
        expected = _frame_tag(detection_key, f, payload)
        bits = 0
        for b in range(_BITS_PER_FRAME):
            bits = (bits << 1) | (pcm16[base + b * 2] & 1)
        if bits == int.from_bytes(expected, "big"):
            ok += 1
    conf = ok / n_frames
    return DetectionResult(present=conf >= 0.9, confidence=conf, frames_checked=n_frames)
