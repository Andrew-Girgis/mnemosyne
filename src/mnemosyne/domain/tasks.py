"""Tasks: create, edit, move, annotate, archive. Every change is audited.

Agents may create, edit, move and annotate; archiving is user-only (spec: agents may
not archive or delete anything).
"""

import json
import sqlite3
import time

from mnemosyne import ids
from mnemosyne.config import Config
from mnemosyne.domain import audit, projects
from mnemosyne.domain.audit import Actor
from mnemosyne.domain.errors import DomainError, forbidden, not_found

EDITABLE = ("title", "description")


def get(conn: sqlite3.Connection, task_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise not_found("task", task_id)
    return row


def _column(config: Config, column: str | None) -> str:
    if column is None:
        return config.task_columns[0]
    for known in config.task_columns:
        if known.lower() == column.lower():
            return known
    raise DomainError(
        422, "invalid_column", f"column must be one of {', '.join(config.task_columns)}"
    )


def create(
    conn: sqlite3.Connection, config: Config, actor: Actor, *, project: str, title: str,
    description: str = "", column: str | None = None,
) -> sqlite3.Row:
    found = projects.get(conn, project)
    if found is None:
        raise not_found("project", project)
    if not title.strip():
        raise DomainError(422, "invalid_title", "title must not be empty")
    now = time.time()
    task_id = ids.new_id("tsk")
    conn.execute(
        "INSERT INTO tasks (id, project_id, title, description, column_name, created_at,"
        " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (task_id, found["id"], title.strip(), description, _column(config, column), now, now),
    )
    row = get(conn, task_id)
    audit.record(conn, actor, "task.create", "task", task_id, after=dict(row))
    return row


def update(conn: sqlite3.Connection, actor: Actor, task_id: str, **fields) -> sqlite3.Row:
    before = get(conn, task_id)
    changes = {k: v for k, v in fields.items() if k in EDITABLE and v is not None}
    if "title" in changes and not changes["title"].strip():
        raise DomainError(422, "invalid_title", "title must not be empty")
    if not changes:
        return before
    sets = ", ".join(f"{k} = ?" for k in changes)
    conn.execute(
        f"UPDATE tasks SET {sets}, updated_at = ? WHERE id = ?",
        [*changes.values(), time.time(), task_id],
    )
    audit.record(conn, actor, "task.update", "task", task_id,
                 before={k: before[k] for k in changes}, after=changes)
    return get(conn, task_id)


def move(
    conn: sqlite3.Connection, config: Config, actor: Actor, task_id: str, column: str
) -> sqlite3.Row:
    before = get(conn, task_id)
    if before["archived_at"]:
        raise DomainError(409, "archived", "task is archived; unarchive it first")
    target = _column(config, column)
    if target == before["column_name"]:
        return before
    conn.execute(
        "UPDATE tasks SET column_name = ?, updated_at = ? WHERE id = ?",
        (target, time.time(), task_id),
    )
    audit.record(conn, actor, "task.move", "task", task_id,
                 before={"column": before["column_name"]}, after={"column": target})
    return get(conn, task_id)


def add_note(conn: sqlite3.Connection, actor: Actor, task_id: str, body: str) -> dict:
    get(conn, task_id)
    if not body.strip():
        raise DomainError(422, "invalid_note", "note must not be empty")
    note_id = ids.new_id("note")
    audit_id = audit.record(conn, actor, "task.note", "task", task_id,
                            after={"note_id": note_id, "body": body})
    now = time.time()
    conn.execute(
        "INSERT INTO task_notes (id, task_id, body, audit_id, created_at) VALUES (?, ?, ?, ?, ?)",
        (note_id, task_id, body, audit_id, now),
    )
    conn.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (now, task_id))
    return {"id": note_id, "task_id": task_id, "body": body, "created_at": now}


def set_archived(conn: sqlite3.Connection, actor: Actor, task_id: str, archived: bool):
    if actor.kind != "user":
        raise forbidden("only the user can archive or unarchive tasks")
    before = get(conn, task_id)
    value = time.time() if archived else None
    conn.execute(
        "UPDATE tasks SET archived_at = ?, updated_at = ? WHERE id = ?",
        (value, time.time(), task_id),
    )
    audit.record(conn, actor, "task.archive" if archived else "task.unarchive", "task", task_id,
                 before={"archived_at": before["archived_at"]}, after={"archived_at": value})
    return get(conn, task_id)


def list_tasks(
    conn: sqlite3.Connection, *, project: str | None = None, column: str | None = None,
    include_archived: bool = False,
) -> list[dict]:
    sql = (
        "SELECT t.*, p.key AS project_key FROM tasks t JOIN projects p ON p.id = t.project_id"
        " WHERE 1=1"
    )
    args: list = []
    if project:
        found = projects.get(conn, project)
        if found is None:
            raise not_found("project", project)
        sql, args = sql + " AND t.project_id = ?", [*args, found["id"]]
    if column:
        sql, args = sql + " AND lower(t.column_name) = lower(?)", [*args, column]
    if not include_archived:
        sql += " AND t.archived_at IS NULL"
    return [dict(r) for r in conn.execute(sql + " ORDER BY t.created_at", args)]


def detail(conn: sqlite3.Connection, task_id: str) -> dict:
    task = dict(get(conn, task_id))
    task["project_key"] = conn.execute(
        "SELECT key FROM projects WHERE id = ?", (task["project_id"],)
    ).fetchone()[0]
    notes = conn.execute(
        "SELECT n.id, n.body, n.created_at, a.actor_kind, a.actor_device, a.actor_harness"
        " FROM task_notes n JOIN audit_log a ON a.id = n.audit_id WHERE n.task_id = ?"
        " ORDER BY n.created_at",
        (task_id,),
    ).fetchall()
    history = conn.execute(
        "SELECT at, actor_kind, actor_device, actor_harness, actor_session, op, before_json,"
        " after_json FROM audit_log WHERE entity_type = 'task' AND entity_id = ? ORDER BY id",
        (task_id,),
    ).fetchall()
    sessions = conn.execute(
        "SELECT harness, session_id, device_id, at FROM session_task_links l WHERE task_id = ?"
        " AND l.id = (SELECT max(id) FROM session_task_links WHERE harness = l.harness"
        " AND session_id = l.session_id) ORDER BY at",
        (task_id,),
    ).fetchall()
    task.update(timelines(conn, [task_id])[task_id])
    return {
        "task": task,
        "notes": [dict(r) for r in notes],
        "history": [dict(r) for r in history],
        "sessions": [dict(r) for r in sessions],
    }


def timelines(conn: sqlite3.Connection, task_ids: list[str]) -> dict[str, dict]:
    """Column entries per task from the audit log: when it was created, first started
    (entered Active), and when it entered its current Paused/Done column. No durations:
    per-task time is not tracked yet."""
    result = {tid: {"column_history": [], "started_at": None, "paused_at": None,
                    "done_at": None} for tid in task_ids}
    if not task_ids:
        return result
    rows = conn.execute(
        f"SELECT entity_id, at, op, after_json FROM audit_log WHERE entity_type = 'task'"
        f" AND op IN ('task.create', 'task.move')"
        f" AND entity_id IN ({','.join('?' * len(task_ids))}) ORDER BY id",
        task_ids,
    ).fetchall()
    for r in rows:
        after = json.loads(r["after_json"] or "{}")
        column = after.get("column_name") if r["op"] == "task.create" else after.get("column")
        if not column:
            continue
        item = result[r["entity_id"]]
        item["column_history"].append({"at": r["at"], "column": column})
        role = column.lower()
        if role == "active" and item["started_at"] is None:
            item["started_at"] = r["at"]
        if role in ("paused", "done"):
            item[f"{role}_at"] = r["at"]
    for item in result.values():
        # Only the current Paused/Done stint counts; a task moved back out has neither.
        current = item["column_history"][-1]["column"].lower() if item["column_history"] else ""
        if current != "paused":
            item["paused_at"] = None
        if current != "done":
            item["done_at"] = None
    return result
