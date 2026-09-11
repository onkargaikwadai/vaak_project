from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from ..voice_model import VoiceIdentityClass
from .principals import PrincipalProfile


class DurableProductState:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS principals(
                    principal_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    body_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS lifecycle_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL,
                    resource_kind TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    body_json TEXT NOT NULL,
                    created_at REAL NOT NULL DEFAULT (unixepoch())
                );
                CREATE TABLE IF NOT EXISTS voice_bindings(
                    principal_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    consent_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS voice_envelopes(
                    principal_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    body_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS voice_lifecycle(
                    principal_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    body_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS studio_characters(
                    character_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    body_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS studio_rights(
                    grant_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    body_json TEXT NOT NULL
                );
                """
            )

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5.0)
        db.row_factory = sqlite3.Row
        return db

    def put_principal(self, p: PrincipalProfile) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO principals(principal_id,tenant_id,body_json) VALUES(?,?,?)",
                (p.principal_id, p.tenant_id, json.dumps(p.body(), sort_keys=True)),
            )

    def get_principal(self, principal_id: str) -> PrincipalProfile | None:
        with self._connect() as db:
            row = db.execute("SELECT body_json FROM principals WHERE principal_id=?", (principal_id,)).fetchone()
        if not row:
            return None
        obj = json.loads(row["body_json"])
        obj["identity_class"] = VoiceIdentityClass(obj["identity_class"])
        return PrincipalProfile(**obj)

    def list_principals(self) -> list[PrincipalProfile]:
        with self._connect() as db:
            rows = db.execute("SELECT body_json FROM principals ORDER BY principal_id").fetchall()
        out = []
        for r in rows:
            obj = json.loads(r["body_json"])
            obj["identity_class"] = VoiceIdentityClass(obj["identity_class"])
            out.append(PrincipalProfile(**obj))
        return out

    def event(self, tenant_id: str, resource_kind: str, resource_id: str, event_type: str, body: dict) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO lifecycle_events(tenant_id,resource_kind,resource_id,event_type,body_json) VALUES(?,?,?,?,?)",
                (tenant_id, resource_kind, resource_id, event_type, json.dumps(body, sort_keys=True)),
            )

    def put_voice_binding(self, tenant_id: str, principal_id: str, asset_id: str, consent: dict) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO voice_bindings(principal_id,tenant_id,asset_id,consent_json) VALUES(?,?,?,?)",
                (principal_id, tenant_id, asset_id, json.dumps(consent, sort_keys=True)),
            )

    def list_voice_bindings(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT principal_id,tenant_id,asset_id,consent_json FROM voice_bindings").fetchall()
        return [dict(principal_id=r["principal_id"], tenant_id=r["tenant_id"], asset_id=r["asset_id"], consent=json.loads(r["consent_json"])) for r in rows]

    def put_envelope(self, tenant_id: str, principal_id: str, envelope: dict) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO voice_envelopes(principal_id,tenant_id,body_json) VALUES(?,?,?)",
                (principal_id, tenant_id, json.dumps(envelope, sort_keys=True)),
            )

    def list_envelopes(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT principal_id,tenant_id,body_json FROM voice_envelopes").fetchall()
        return [dict(principal_id=r["principal_id"], tenant_id=r["tenant_id"], envelope=json.loads(r["body_json"])) for r in rows]

    def put_voice_lifecycle(self, tenant_id: str, principal_id: str, body: dict) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO voice_lifecycle(principal_id,tenant_id,body_json) VALUES(?,?,?)",
                (principal_id, tenant_id, json.dumps(body, sort_keys=True)),
            )

    def list_voice_lifecycle(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT principal_id,tenant_id,body_json FROM voice_lifecycle").fetchall()
        return [dict(principal_id=r["principal_id"], tenant_id=r["tenant_id"], lifecycle=json.loads(r["body_json"])) for r in rows]

    def put_studio_character(self, tenant_id: str, character_id: str, body: dict) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO studio_characters(character_id,tenant_id,body_json) VALUES(?,?,?)",
                (character_id, tenant_id, json.dumps(body, sort_keys=True)),
            )

    def list_studio_characters(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT character_id,tenant_id,body_json FROM studio_characters").fetchall()
        return [dict(character_id=r["character_id"], tenant_id=r["tenant_id"], character=json.loads(r["body_json"])) for r in rows]

    def put_studio_rights(self, tenant_id: str, grant_id: str, body: dict) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO studio_rights(grant_id,tenant_id,body_json) VALUES(?,?,?)",
                (grant_id, tenant_id, json.dumps(body, sort_keys=True)),
            )

    def list_studio_rights(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT grant_id,tenant_id,body_json FROM studio_rights").fetchall()
        return [dict(grant_id=r["grant_id"], tenant_id=r["tenant_id"], grant=json.loads(r["body_json"])) for r in rows]
