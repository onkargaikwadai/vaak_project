from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Mapping

from .contracts import ASRBackend, AudioChunk, StreamingTTSBackend, Transcript, VoiceProfile


class QualityGateError(RuntimeError):
    pass


@dataclass(frozen=True)
class QualitySnapshot:
    provider_id: str
    role: str
    quality_score: float
    latency_p95_ms: float
    evaluated_at: float = field(default_factory=time.time)
    identity_similarity: float | None = None
    wer: float | None = None
    agency_false_negative_rate: float | None = None
    availability: float = 1.0
    cost_score: float = 0.5
    notes: str = ""

    def body(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "role": self.role,
            "quality_score": self.quality_score,
            "latency_p95_ms": self.latency_p95_ms,
            "evaluated_at": self.evaluated_at,
            "identity_similarity": self.identity_similarity,
            "wer": self.wer,
            "agency_false_negative_rate": self.agency_false_negative_rate,
            "availability": self.availability,
            "cost_score": self.cost_score,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class QualityPolicy:
    role: str
    min_quality_score: float = 0.80
    max_latency_p95_ms: float = 1200.0
    min_availability: float = 0.95
    min_identity_similarity: float | None = None
    max_wer: float | None = None
    max_agency_false_negative_rate: float | None = None
    freshness_seconds: float = 30 * 24 * 3600
    quality_weight: float = 0.70
    latency_weight: float = 0.20
    cost_weight: float = 0.10

    def accepts(self, s: QualitySnapshot, *, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        if s.role != self.role:
            return False
        if now - s.evaluated_at > self.freshness_seconds:
            return False
        if s.quality_score < self.min_quality_score:
            return False
        if s.latency_p95_ms > self.max_latency_p95_ms:
            return False
        if s.availability < self.min_availability:
            return False
        if self.min_identity_similarity is not None:
            if s.identity_similarity is None or s.identity_similarity < self.min_identity_similarity:
                return False
        if self.max_wer is not None:
            if s.wer is None or s.wer > self.max_wer:
                return False
        if self.max_agency_false_negative_rate is not None:
            if s.agency_false_negative_rate is None or s.agency_false_negative_rate > self.max_agency_false_negative_rate:
                return False
        return True


DEFAULT_POLICIES = {
    "tts": QualityPolicy(
        role="tts",
        min_quality_score=0.86,
        min_identity_similarity=0.84,
        max_latency_p95_ms=700.0,
        quality_weight=0.78,
        latency_weight=0.17,
        cost_weight=0.05,
    ),
    "asr": QualityPolicy(
        role="asr",
        min_quality_score=0.84,
        max_wer=0.16,
        max_latency_p95_ms=650.0,
        quality_weight=0.72,
        latency_weight=0.23,
        cost_weight=0.05,
    ),
    "agency": QualityPolicy(
        role="agency",
        min_quality_score=0.90,
        max_agency_false_negative_rate=0.04,
        max_latency_p95_ms=850.0,
        quality_weight=0.88,
        latency_weight=0.10,
        cost_weight=0.02,
    ),
}


class QualityStore:
    """Durable benchmark registry used by the production model router.

    The store contains evaluation results, not marketing claims. Production
    routes only to providers with a fresh snapshot that clears the configured
    policy for the requested role.
    """

    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS quality_snapshots (
                    provider_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    body_json TEXT NOT NULL,
                    evaluated_at REAL NOT NULL,
                    PRIMARY KEY(provider_id, role)
                )"""
            )

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5.0)
        db.row_factory = sqlite3.Row
        return db

    def put(self, snapshot: QualitySnapshot) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO quality_snapshots(provider_id, role, body_json, evaluated_at) VALUES(?,?,?,?)",
                (snapshot.provider_id, snapshot.role, json.dumps(snapshot.body(), sort_keys=True), snapshot.evaluated_at),
            )

    def get(self, provider_id: str, role: str) -> QualitySnapshot | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT body_json FROM quality_snapshots WHERE provider_id=? AND role=?",
                (provider_id, role),
            ).fetchone()
        if not row:
            return None
        return QualitySnapshot(**json.loads(row["body_json"]))

    def list_role(self, role: str) -> list[QualitySnapshot]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT body_json FROM quality_snapshots WHERE role=? ORDER BY evaluated_at DESC",
                (role,),
            ).fetchall()
        return [QualitySnapshot(**json.loads(r["body_json"])) for r in rows]


class QualityRouter:
    def __init__(self, store: QualityStore, policies: Mapping[str, QualityPolicy] | None = None):
        self.store = store
        self.policies = dict(DEFAULT_POLICIES if policies is None else policies)

    def ranked(self, role: str, provider_ids: list[str]) -> list[str]:
        policy = self.policies[role]
        accepted: list[tuple[float, str]] = []
        rejected: list[str] = []
        for provider_id in provider_ids:
            snap = self.store.get(provider_id, role)
            if snap is None or not policy.accepts(snap):
                rejected.append(provider_id)
                continue
            latency_score = max(0.0, 1.0 - snap.latency_p95_ms / max(policy.max_latency_p95_ms, 1.0))
            score = (
                policy.quality_weight * snap.quality_score
                + policy.latency_weight * latency_score
                + policy.cost_weight * snap.cost_score
            )
            accepted.append((score, provider_id))
        if not accepted:
            raise QualityGateError(
                f"no {role} provider clears quality policy; rejected={','.join(rejected) or 'none configured'}"
            )
        accepted.sort(reverse=True)
        return [pid for _, pid in accepted]

    def status(self, role: str, provider_ids: list[str]) -> dict:
        policy = self.policies[role]
        rows = []
        for pid in provider_ids:
            snap = self.store.get(pid, role)
            rows.append({
                "provider_id": pid,
                "eligible": bool(snap and policy.accepts(snap)),
                "snapshot": snap.body() if snap else None,
            })
        return {"role": role, "policy": policy.__dict__, "providers": rows}


class RoutedASR:
    def __init__(self, providers: Mapping[str, ASRBackend], router: QualityRouter):
        self.providers = dict(providers)
        self.router = router

    async def transcribe(self, pcm16: bytes, *, sample_rate: int = 16000, language: str | None = None) -> Transcript:
        errors: list[str] = []
        for provider_id in self.router.ranked("asr", list(self.providers)):
            try:
                return await self.providers[provider_id].transcribe(pcm16, sample_rate=sample_rate, language=language)
            except Exception as exc:
                errors.append(f"{provider_id}:{type(exc).__name__}")
        raise QualityGateError("all eligible ASR providers failed: " + ",".join(errors))


class RoutedTTS:
    """Quality-gated TTS with failover only before first emitted audio.

    Switching voices/backbones mid-utterance can violate identity continuity and
    provenance expectations, so a provider failure after first audio is emitted
    fails closed instead of silently switching models.
    """

    def __init__(self, providers: Mapping[str, StreamingTTSBackend], router: QualityRouter):
        self.providers = dict(providers)
        self.router = router
        self.sample_rate = 16000
        self.last_provider_id: str | None = None

    async def synthesize(
        self,
        text: str,
        *,
        voice: VoiceProfile,
        language: str = "English",
        prosody: dict | None = None,
        conditioning: dict | None = None,
    ) -> AsyncIterator[AudioChunk]:
        errors: list[str] = []
        for provider_id in self.router.ranked("tts", list(self.providers)):
            emitted = False
            try:
                backend = self.providers[provider_id]
                async for chunk in backend.synthesize(
                    text,
                    voice=voice,
                    language=language,
                    prosody=prosody,
                    conditioning=conditioning,
                ):
                    emitted = True
                    self.last_provider_id = provider_id
                    self.sample_rate = chunk.sample_rate
                    yield chunk
                return
            except Exception as exc:
                if emitted:
                    raise QualityGateError(
                        f"TTS provider {provider_id} failed after emission; mid-utterance failover prohibited"
                    ) from exc
                errors.append(f"{provider_id}:{type(exc).__name__}")
        raise QualityGateError("all eligible TTS providers failed: " + ",".join(errors))
