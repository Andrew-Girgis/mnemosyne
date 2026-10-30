"""Audit trail for every change to editable records."""

import json
import sqlite3
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Actor:
    kind: str  # 'user' | 'agent' | 'system'
    device: str | None = None
    harness: str | None = None
    session: str | None = None


SYSTEM = Actor("system")


def record(
    conn: sqlite3.Connection,
    actor: Actor,
    op: str,
    entity_type: str,
    entity_id: str,
    before: dict | None = None,
    after: dict | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO audit_log (at, actor_kind, actor_device, actor_harness, actor_session,"
        " op, entity_type, entity_id, before_json, after_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            time.time(),
            actor.kind,
            actor.device,
            actor.harness,
            actor.session,
            op,
            entity_type,
            entity_id,
            json.dumps(before) if before is not None else None,
            json.dumps(after) if after is not None else None,
        ),
    )
    return cur.lastrowid
