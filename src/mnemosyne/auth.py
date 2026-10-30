"""API tokens. Only sha256 hashes are stored; the token is shown once at creation."""

import hashlib
import secrets
import sqlite3
import time
from dataclasses import dataclass

from mnemosyne import ids

KINDS = ("user", "collector", "agent")


@dataclass(frozen=True)
class Principal:
    token_id: str
    kind: str  # 'user' | 'collector' | 'agent'
    device_id: str | None


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_token(
    conn: sqlite3.Connection, kind: str, device_id: str | None, label: str | None
) -> tuple[str, str]:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    if kind in ("collector", "agent", "user") and device_id is None:
        raise ValueError("tokens are per device; pass a device")
    token = f"mn_{kind}_{secrets.token_urlsafe(32)}"
    token_id = ids.new_id("tok")
    conn.execute(
        "INSERT INTO api_tokens (id, token_hash, kind, device_id, label, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (token_id, _hash(token), kind, device_id, label, time.time()),
    )
    return token_id, token


def authenticate(conn: sqlite3.Connection, token: str) -> Principal | None:
    row = conn.execute(
        "SELECT id, kind, device_id FROM api_tokens WHERE token_hash = ? AND revoked_at IS NULL",
        (_hash(token),),
    ).fetchone()
    return Principal(row["id"], row["kind"], row["device_id"]) if row else None


def revoke(conn: sqlite3.Connection, token_id: str) -> bool:
    cur = conn.execute(
        "UPDATE api_tokens SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
        (time.time(), token_id),
    )
    return cur.rowcount == 1
