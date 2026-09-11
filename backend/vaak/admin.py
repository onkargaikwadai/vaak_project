from __future__ import annotations

import argparse
import json

from .product.config import ProductConfig
from .product.security import TenantAuthStore


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vaak-admin", description="Parinita Vaak operator administration")
    p.add_argument("--auth-db", default=None, help="Auth SQLite path; defaults to VAAK_AUTH_DB/ProductConfig")
    sub = p.add_subparsers(dest="command", required=True)

    issue = sub.add_parser("issue-token", help="Issue a tenant-scoped bearer token")
    issue.add_argument("--tenant", required=True)
    issue.add_argument("--subject", required=True)
    issue.add_argument("--scope", action="append", default=[], help="Repeatable scope; defaults to '*'")

    disable = sub.add_parser("disable-token", help="Disable a token by token_id")
    disable.add_argument("--token-id", required=True)

    bind = sub.add_parser("bind-resource", help="Bind a resource to a tenant")
    bind.add_argument("--tenant", required=True)
    bind.add_argument("--kind", required=True)
    bind.add_argument("--id", required=True)
    bind.add_argument("--controller", default=None)
    return p


def main() -> None:
    args = _parser().parse_args()
    cfg = ProductConfig.from_env()
    store = TenantAuthStore(args.auth_db or cfg.auth_db_path)
    if args.command == "issue-token":
        scopes = tuple(args.scope) if args.scope else ("*",)
        token = store.issue_token(args.tenant, args.subject, scopes)
        print(json.dumps({"tenant_id": args.tenant, "subject_id": args.subject, "scopes": list(scopes), "token": token}))
    elif args.command == "disable-token":
        store.disable_token(args.token_id)
        print(json.dumps({"token_id": args.token_id, "disabled": True}))
    elif args.command == "bind-resource":
        store.bind_resource(args.tenant, args.kind, args.id, args.controller)
        print(json.dumps({"tenant_id": args.tenant, "resource_kind": args.kind, "resource_id": args.id, "controller_id": args.controller, "bound": True}))


if __name__ == "__main__":
    main()
