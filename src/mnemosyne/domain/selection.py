"""Per-device selection (the user's focus) and per-session agent task context.

The device selection is the default attribution for ambiguous app time on that device;
only the user (or that device's collector, for lock/idle/sleep) changes it. Agents get a
session-scoped link instead, which never touches the device selection.
"""

import sqlite3
import time

from mnemosyne.domain import audit, projects, tasks
from mnemosyne.domain.audit import Actor
from mnemosyne.domain.errors import DomainError, forbidden, not_found

COLLECTOR_REASONS = ("lock", "sleep", "idle")


def current(conn: sqlite3.Connection, device_id: str | None = None) -> list[dict]:
    sql = (
        "SELECT e.device_id, e.at, e.reason, e.project_id, p.key AS project_key, e.task_id,"
        " t.title AS task_title, t.column_name AS task_column FROM selection_events e"
        " LEFT JOIN projects p ON p.id = e.project_id LEFT JOIN tasks t ON t.id = e.task_id"
        " WHERE e.id = (SELECT max(id) FROM selection_events WHERE device_id = e.device_id)"
    )
    args: list = []
    if device_id:
        sql, args = sql + " AND e.device_id = ?", [device_id]
    return [dict(r) for r in conn.execute(sql + " ORDER BY e.device_id", args)]


def select(
    conn: sqlite3.Connection, actor: Actor, device_id: str, *, project: str | None = None,
    task: str | None = None,
) -> dict:
    if actor.kind != "user":
        raise forbidden("only the user changes a device's selection; agents link their session")
    project_row = task_row = None
    if task:
        task_row = tasks.get(conn, task)
        if task_row["archived_at"]:
            raise DomainError(409, "archived", "cannot select an archived task")
        project_row = conn.execute(
            "SELECT * FROM projects WHERE id = ?", (task_row["project_id"],)
        ).fetchone()
    elif project:
        project_row = projects.get(conn, project)
        if project_row is None:
            raise not_found("project", project)
    else:
        raise DomainError(422, "nothing_selected", "pass a task or a project (or use clear)")
    return _event(conn, actor, device_id, project_row["id"],
                  task_row["id"] if task_row else None, "user")


def clear(conn: sqlite3.Connection, actor: Actor, device_id: str, reason: str = "clear") -> dict:
    if actor.kind == "agent":
        raise forbidden("agents cannot change a device's selection")
    if reason != "clear" and reason not in COLLECTOR_REASONS:
        raise DomainError(422, "invalid_reason", f"reason must be clear or {COLLECTOR_REASONS}")
    return _event(conn, actor, device_id, None, None, reason)


def _event(conn, actor, device_id, project_id, task_id, reason) -> dict:
    now = time.time()
    conn.execute(
        "INSERT INTO selection_events (device_id, at, project_id, task_id, reason)"
        " VALUES (?, ?, ?, ?, ?)",
        (device_id, now, project_id, task_id, reason),
    )
    audit.record(conn, actor, "selection.set" if project_id else "selection.clear",
                 "device", device_id,
                 after={"project_id": project_id, "task_id": task_id, "reason": reason})
    return current(conn, device_id)[0]


def link_session(
    conn: sqlite3.Connection, actor: Actor, *, harness: str, session_id: str,
    task: str | None,
) -> dict:
    """Link an agent session to a task (or unlink with task=None). Agents may only link
    their own session; the user may link any session (e.g. accepting a TUI suggestion)."""
    if not harness or not session_id:
        raise DomainError(422, "no_session", "harness and session id are required")
    if actor.kind == "agent" and (actor.harness != harness or actor.session != session_id):
        raise forbidden("agents may only link their own session")
    task_row = tasks.get(conn, task) if task else None
    audit_id = audit.record(
        conn, actor, "session.link", "session", f"{harness}:{session_id}",
        after={"task_id": task_row["id"] if task_row else None},
    )
    conn.execute(
        "INSERT INTO session_task_links (harness, session_id, device_id, project_id, task_id,"
        " at, actor_kind, audit_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (harness, session_id, actor.device, task_row["project_id"] if task_row else None,
         task_row["id"] if task_row else None, time.time(),
         "agent" if actor.kind == "agent" else "user", audit_id),
    )
    return {"harness": harness, "session_id": session_id,
            "task_id": task_row["id"] if task_row else None}
