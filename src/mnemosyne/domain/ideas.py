"""Ideas: create, edit, drop/reopen, and start (link to the project it became).

Agents may create and edit ideas; dropping and reopening are user-only (spec: agents may
not archive or delete anything). Every change is audited.
"""

import sqlite3
import time

from mnemosyne import ids
from mnemosyne.config import Config
from mnemosyne.domain import audit, projects
from mnemosyne.domain.audit import Actor
from mnemosyne.domain.errors import DomainError, forbidden, not_found

EDITABLE = ("title", "summary", "body", "workspace")
STATUSES = ("open", "started", "dropped")


def get(conn: sqlite3.Connection, idea_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM ideas WHERE id = ?", (idea_id,)).fetchone()
    if row is None:
        raise not_found("idea", idea_id)
    return row


def _workspace(config: Config, workspace: str) -> str:
    if workspace not in config.workspaces:
        raise DomainError(422, "invalid_workspace",
                          f"workspace must be one of {', '.join(config.workspaces)}")
    return workspace


def _title(title: str) -> str:
    if not title.strip():
        raise DomainError(422, "invalid_title", "title must not be empty")
    return title.strip()


def create(
    conn: sqlite3.Connection, config: Config, actor: Actor, *, title: str, workspace: str,
    summary: str = "", body: str = "",
) -> sqlite3.Row:
    now = time.time()
    idea_id = ids.new_id("ida")
    conn.execute(
        "INSERT INTO ideas (id, workspace, title, summary, body, status, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, 'open', ?, ?)",
        (idea_id, _workspace(config, workspace), _title(title), summary.strip(), body, now, now),
    )
    row = get(conn, idea_id)
    audit.record(conn, actor, "idea.create", "idea", idea_id, after=dict(row))
    return row


def update(conn: sqlite3.Connection, config: Config, actor: Actor, idea_id: str,
           **fields) -> sqlite3.Row:
    before = get(conn, idea_id)
    changes = {k: v for k, v in fields.items() if k in EDITABLE and v is not None}
    if "title" in changes:
        changes["title"] = _title(changes["title"])
    if "summary" in changes:
        changes["summary"] = changes["summary"].strip()
    if "workspace" in changes:
        _workspace(config, changes["workspace"])
    changes = {k: v for k, v in changes.items() if v != before[k]}
    if not changes:
        return before
    sets = ", ".join(f"{k} = ?" for k in changes)
    conn.execute(f"UPDATE ideas SET {sets}, updated_at = ? WHERE id = ?",
                 [*changes.values(), time.time(), idea_id])
    audit.record(conn, actor, "idea.update", "idea", idea_id,
                 before={k: before[k] for k in changes}, after=changes)
    return get(conn, idea_id)


def set_dropped(conn: sqlite3.Connection, actor: Actor, idea_id: str, dropped: bool):
    if actor.kind != "user":
        raise forbidden("only the user can drop or reopen ideas")
    before = get(conn, idea_id)
    if before["status"] == "started":
        raise DomainError(409, "started", "idea already became a project")
    status, value = ("dropped", time.time()) if dropped else ("open", None)
    if before["status"] == status:
        return before
    conn.execute("UPDATE ideas SET status = ?, dropped_at = ?, updated_at = ? WHERE id = ?",
                 (status, value, time.time(), idea_id))
    audit.record(conn, actor, "idea.drop" if dropped else "idea.reopen", "idea", idea_id,
                 before={"status": before["status"]}, after={"status": status})
    return get(conn, idea_id)


def start(conn: sqlite3.Connection, actor: Actor, idea_id: str, project: str) -> sqlite3.Row:
    """Link an open idea to the project it became. A project with no description yet takes
    the idea's summary and body as its description."""
    idea = get(conn, idea_id)
    if idea["status"] != "open":
        raise DomainError(409, "not_open", f"idea is {idea['status']}, not open")
    found = projects.get(conn, project)
    if found is None:
        raise not_found("project", project)
    now = time.time()
    conn.execute(
        "UPDATE ideas SET status = 'started', project_id = ?, started_at = ?, updated_at = ?"
        " WHERE id = ?",
        (found["id"], now, now, idea_id),
    )
    audit.record(conn, actor, "idea.start", "idea", idea_id,
                 before={"status": idea["status"]},
                 after={"status": "started", "project_id": found["id"]})
    text = "\n\n".join(part for part in (idea["summary"], idea["body"].strip()) if part)
    if text and not found["description"]:
        projects.set_description(conn, actor, found["id"], text)
    return get(conn, idea_id)


def list_ideas(conn: sqlite3.Connection, *, status: str | None = "open",
               workspace: str | None = None) -> list[dict]:
    sql = ("SELECT i.*, p.key AS project_key, p.name AS project_name FROM ideas i"
           " LEFT JOIN projects p ON p.id = i.project_id WHERE 1=1")
    args: list = []
    if status:
        if status not in STATUSES:
            raise DomainError(422, "invalid_status", f"status must be one of {STATUSES}")
        sql, args = sql + " AND i.status = ?", [*args, status]
    if workspace:
        sql, args = sql + " AND i.workspace = ?", [*args, workspace]
    return [dict(r) for r in conn.execute(sql + " ORDER BY i.created_at DESC", args)]


def for_project(conn: sqlite3.Connection, project_id: str) -> dict | None:
    row = conn.execute(
        "SELECT id, title, summary, started_at FROM ideas WHERE project_id = ?"
        " ORDER BY started_at LIMIT 1",
        (project_id,),
    ).fetchone()
    return dict(row) if row else None


def detail(conn: sqlite3.Connection, idea_id: str) -> dict:
    idea = get(conn, idea_id)
    project = projects.get(conn, idea["project_id"]) if idea["project_id"] else None
    history = conn.execute(
        "SELECT at, actor_kind, actor_device, actor_harness, op FROM audit_log"
        " WHERE entity_type = 'idea' AND entity_id = ? ORDER BY id",
        (idea_id,),
    ).fetchall()
    return {
        "idea": dict(idea),
        "project": {"id": project["id"], "key": project["key"], "name": project["name"]}
        if project else None,
        "history": [dict(r) for r in history],
    }
