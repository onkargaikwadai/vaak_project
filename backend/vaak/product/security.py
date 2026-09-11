from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path


class AuthorizationError(PermissionError):
    pass


@dataclass(frozen=True)
class AuthContext:
    tenant_id: str
    subject_id: str
    scopes: tuple[str, ...]
    token_id: str

    def allows(self, scope: str) -> bool:
        return "*" in self.scopes or scope in self.scopes


class TenantAuthStore:
    """Durable tenant-scoped bearer token and object ownership store."""

    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS auth_tokens(
                    token_hash TEXT PRIMARY KEY,
                    token_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    scopes_json TEXT NOT NULL,
                    disabled_at REAL
                );
                CREATE TABLE IF NOT EXISTS resource_owners(
                    resource_kind TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    controller_id TEXT,
                    PRIMARY KEY(resource_kind, resource_id)
                );
                CREATE TABLE IF NOT EXISTS auth_rate_windows(
                    token_id TEXT NOT NULL,
                    window_minute INTEGER NOT NULL,
                    request_count INTEGER NOT NULL,
                    PRIMARY KEY(token_id, window_minute)
                );
                """
            )
            cols = {r[1] for r in db.execute("PRAGMA table_info(resource_owners)").fetchall()}
            if "controller_id" not in cols:
                db.execute("ALTER TABLE resource_owners ADD COLUMN controller_id TEXT")

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5.0)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def issue_token(self, tenant_id: str, subject_id: str, scopes: tuple[str, ...] = ("*",), *, token: str | None = None) -> str:
        raw = token or secrets.token_urlsafe(36)
        token_id = "tok_" + secrets.token_hex(10)
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO auth_tokens(token_hash, token_id, tenant_id, subject_id, scopes_json, disabled_at) VALUES(?,?,?,?,?,NULL)",
                (self._hash(raw), token_id, tenant_id, subject_id, json.dumps(list(scopes))),
            )
        return raw

    def authenticate(self, token: str) -> AuthContext:
        with self._connect() as db:
            row = db.execute(
                "SELECT token_id,tenant_id,subject_id,scopes_json,disabled_at FROM auth_tokens WHERE token_hash=?",
                (self._hash(token),),
            ).fetchone()
        if not row or row["disabled_at"] is not None:
            raise AuthorizationError("invalid bearer token")
        return AuthContext(row["tenant_id"], row["subject_id"], tuple(json.loads(row["scopes_json"])), row["token_id"])

    def disable_token(self, token_id: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("UPDATE auth_tokens SET disabled_at=? WHERE token_id=?", (time.time(), token_id))

    def bind_resource(self, tenant_id: str, resource_kind: str, resource_id: str, controller_id: str | None = None) -> None:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT tenant_id,controller_id FROM resource_owners WHERE resource_kind=? AND resource_id=?",
                (resource_kind, resource_id),
            ).fetchone()
            if row and row["tenant_id"] != tenant_id:
                raise AuthorizationError("resource already belongs to another tenant")
            if row and row["controller_id"] and controller_id and row["controller_id"] != controller_id:
                raise AuthorizationError("resource already belongs to another controller")
            db.execute(
                "INSERT OR IGNORE INTO resource_owners(resource_kind,resource_id,tenant_id,controller_id) VALUES(?,?,?,?)",
                (resource_kind, resource_id, tenant_id, controller_id),
            )
            if row and not row["controller_id"] and controller_id:
                db.execute(
                    "UPDATE resource_owners SET controller_id=? WHERE resource_kind=? AND resource_id=?",
                    (controller_id, resource_kind, resource_id),
                )

    def require_resource(self, ctx: AuthContext, resource_kind: str, resource_id: str, scope: str) -> None:
        if not ctx.allows(scope):
            raise AuthorizationError(f"missing scope: {scope}")
        with self._connect() as db:
            row = db.execute(
                "SELECT tenant_id,controller_id FROM resource_owners WHERE resource_kind=? AND resource_id=?",
                (resource_kind, resource_id),
            ).fetchone()
        if not row or row["tenant_id"] != ctx.tenant_id:
            raise AuthorizationError("resource not visible to tenant")
        controller_id = row["controller_id"]
        if controller_id and controller_id != ctx.subject_id and not ctx.allows("tenant:admin") and "*" not in ctx.scopes:
            raise AuthorizationError("resource not controlled by authenticated subject")

    def check_rate(self, ctx: AuthContext, *, limit_per_minute: int = 1200) -> None:
        if limit_per_minute <= 0:
            return
        minute = int(time.time() // 60)
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT request_count FROM auth_rate_windows WHERE token_id=? AND window_minute=?",
                (ctx.token_id, minute),
            ).fetchone()
            count = int(row["request_count"]) if row else 0
            if count >= limit_per_minute:
                raise AuthorizationError("rate limit exceeded")
            db.execute(
                "INSERT OR REPLACE INTO auth_rate_windows(token_id,window_minute,request_count) VALUES(?,?,?)",
                (ctx.token_id, minute, count + 1),
            )
            db.execute("DELETE FROM auth_rate_windows WHERE window_minute < ?", (minute - 2,))
