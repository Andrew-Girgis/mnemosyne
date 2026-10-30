"""HTTP API. Every operation (CLI, TUI, collectors, widgets) goes through here.

Token kinds: `user` (everything), `agent` (read + task/note/project-create + own session
link), `collector` (scans, selection expiry). Agent identity (harness, session) comes from
X-Mnemosyne-Harness / X-Mnemosyne-Session and is recorded in the audit log.
"""

import asyncio
import logging
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from mnemosyne import auth, devices
from mnemosyne.config import Config
from mnemosyne.db import connect, migrate
from mnemosyne.domain import commits, heartbeats, ideas, projects, selection, tasks
from mnemosyne.domain.audit import Actor
from mnemosyne.domain.errors import DomainError
from mnemosyne.importers import wakapi

log = logging.getLogger("mnemosyne")

ANY_READER = ("user", "agent")


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


# --- request bodies ------------------------------------------------------------------


class ScanEntryIn(BaseModel):
    workspace: str
    relpath: str
    path: str
    kind: str
    has_git: bool
    git_root_commit: str | None = None
    newest_mtime: float | None = None


class ScanLinkIn(BaseModel):
    workspace: str
    relpath: str
    target_path: str


class CommitIn(BaseModel):
    sha: str
    authored_at: float
    committed_at: float
    author_email: str | None = None
    subject: str | None = None


class RepoIn(BaseModel):
    path: str
    name: str
    root_commit: str | None = None
    mirror_workspace: str | None = None
    commits: list[CommitIn] = []


class ScanIn(BaseModel):
    complete: bool
    entries: list[ScanEntryIn]
    links: list[ScanLinkIn] = []
    repos: list[RepoIn] = []


class MergeIn(BaseModel):
    into: str


class ProjectIn(BaseModel):
    name: str
    workspace: str
    lifecycle: str = "active"


class ProjectPatch(BaseModel):
    lifecycle: str | None = None
    description: str | None = None


class IdeaIn(BaseModel):
    title: str
    workspace: str
    summary: str = ""
    body: str = ""


class IdeaPatch(BaseModel):
    title: str | None = None
    workspace: str | None = None
    summary: str | None = None
    body: str | None = None


class IdeaStart(BaseModel):
    project: str


class SuggestionDecision(BaseModel):
    accept: bool


class TaskIn(BaseModel):
    project: str
    title: str
    description: str = ""
    column: str | None = None


class TaskPatch(BaseModel):
    title: str | None = None
    description: str | None = None


class MoveIn(BaseModel):
    column: str


class NoteIn(BaseModel):
    body: str


class SelectIn(BaseModel):
    task: str | None = None
    project: str | None = None


class SessionLinkIn(BaseModel):
    task: str | None = None


@dataclass(frozen=True)
class Ctx:
    conn: sqlite3.Connection
    principal: auth.Principal
    actor: Actor


def create_app(config: Config, db_path: str, *, background: bool = True) -> FastAPI:
    setup = connect(db_path)
    migrate(setup)
    devices.sync(setup, config)
    with tx(setup):  # a rule-version bump re-derives the history it affects, once
        rederived = heartbeats.derive_outdated(setup, config)
    if rederived:
        log.info("re-derived %d heartbeats under the current rules", rederived)
    setup.execute(
        "UPDATE import_runs SET error = 'interrupted', finished_at = ?"
        " WHERE finished_at IS NULL",
        (time.time(),),
    )
    setup.close()
    import_lock = threading.Lock()  # never two Wakapi imports at once
    tz = ZoneInfo(config.timezone)

    def db():
        # One connection per request; WAL + BEGIN IMMEDIATE serialise writers.
        conn = connect(db_path)
        try:
            yield conn
        finally:
            conn.close()

    def run_wakapi_import(from_start: bool = False) -> dict:
        with import_lock:
            conn = connect(db_path)
            try:
                result = wakapi.run(conn, config, from_start=from_start)
                heartbeats.derive_pending(conn, config, include_unmatched=True)
                return result
            finally:
                conn.close()

    async def import_loop():
        while True:
            try:
                await asyncio.to_thread(run_wakapi_import)
            except Exception as exc:  # noqa: BLE001 — recorded in import_runs; keep polling
                log.warning("wakapi import failed: %s", exc)
            await asyncio.sleep(config.wakapi.poll_seconds)

    @asynccontextmanager
    async def lifespan(_app):
        task = None
        if background and config.wakapi.api_key:
            task = asyncio.create_task(import_loop())
        yield
        if task:
            task.cancel()

    app = FastAPI(title="Mnemosyne", version="0.2.0", lifespan=lifespan)

    @app.exception_handler(DomainError)
    async def _domain_error(_request: Request, exc: DomainError):
        return _error(exc.status, exc.code, exc.message)

    @app.exception_handler(HTTPException)
    async def _http_error(_request: Request, exc: HTTPException):
        return _error(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError):
        return _error(422, "invalid_request", str(exc.errors()))

    def ctx(*kinds: str):
        def build(
            authorization: str | None = Header(default=None),
            x_mnemosyne_harness: str | None = Header(default=None),
            x_mnemosyne_session: str | None = Header(default=None),
            conn: sqlite3.Connection = Depends(db),
        ) -> Ctx:
            if not authorization or not authorization.startswith("Bearer "):
                raise DomainError(401, "unauthenticated", "missing bearer token")
            p = auth.authenticate(conn, authorization.removeprefix("Bearer ").strip())
            if p is None:
                raise DomainError(401, "unauthenticated", "unknown or revoked token")
            if p.kind not in kinds:
                raise DomainError(403, "forbidden", f"{p.kind} tokens cannot do this")
            if p.kind == "agent":
                actor = Actor("agent", p.device_id, x_mnemosyne_harness, x_mnemosyne_session)
            elif p.kind == "user":
                actor = Actor("user", p.device_id)
            else:
                actor = Actor("system", p.device_id, "collector")
            return Ctx(conn, p, actor)
        return build

    def project_or_404(conn, ref: str) -> sqlite3.Row:
        found = projects.get(conn, ref)
        if found is None:
            raise DomainError(404, "not_found", f"no project {ref!r}")
        return found

    def window(day: str | None, days: int) -> tuple[date, float, float]:
        d = date.fromisoformat(day) if day else datetime.now(tz).date()
        end = datetime(d.year, d.month, d.day, tzinfo=tz) + timedelta(days=1)
        return d, (end - timedelta(days=days)).timestamp(), end.timestamp()

    # --- status & config ----------------------------------------------------------

    @app.get("/health")
    def health(conn: sqlite3.Connection = Depends(db)):
        last = conn.execute(
            "SELECT started_at, finished_at, inserted, error FROM import_runs"
            " WHERE source = 'wakapi' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "status": "ok",
            "time": time.time(),
            "wakapi_import": dict(last) if last else None,
            "wakapi_configured": bool(config.wakapi.api_key),
        }

    @app.get("/v1/whoami")
    def whoami(c: Ctx = Depends(ctx("user", "agent", "collector"))):
        return {"token_kind": c.principal.kind, "device": c.principal.device_id,
                "actor": c.actor.__dict__}

    @app.get("/v1/config/board")
    def board_config(c: Ctx = Depends(ctx(*ANY_READER))):
        return {"columns": list(config.task_columns), "timezone": config.timezone,
                "workspaces": list(config.workspaces), "devices": list(config.devices),
                "lifecycles": list(projects.LIFECYCLES)}

    @app.get("/v1/config/scan")
    def scan_config(c: Ctx = Depends(ctx("user", "collector"))):
        device = config.devices.get(c.principal.device_id or "")
        if device is None:
            raise DomainError(400, "unknown_device",
                              f"token device {c.principal.device_id!r} not configured")
        return {
            "device": device.id,
            "workspaces": [
                {
                    "name": ws.name,
                    "base": device.expand(ws.base),
                    "try_root": device.expand(ws.try_root),
                    "mirror_root": device.expand(ws.mirror_root) if ws.mirror_root else None,
                }
                for ws in config.workspaces.values()
            ],
        }

    # --- scans -------------------------------------------------------------------------

    @app.post("/v1/scans")
    def post_scan(body: ScanIn, c: Ctx = Depends(ctx("user", "collector"))):
        entries = [projects.ScanEntry(**e.model_dump()) for e in body.entries]
        for e in entries:
            if e.workspace not in config.workspaces or e.kind not in ("try", "graduated"):
                raise DomainError(422, "invalid_entry", f"bad workspace/kind for {e.path}")
        result = projects.ingest_scan(
            c.conn, config, c.principal.device_id, entries, body.complete, c.actor,
            links=[link.model_dump() for link in body.links],
        )
        with tx(c.conn):
            commit_result = commits.ingest(c.conn, config, c.principal.device_id,
                                           [r.model_dump() for r in body.repos])
        rederived = heartbeats.derive_pending(c.conn, config, include_unmatched=True)
        return {**result.__dict__, "commits": commit_result, "heartbeats_rederived": rederived}

    # --- projects ------------------------------------------------------------------------

    @app.get("/v1/projects")
    def list_projects(workspace: str | None = None, lifecycle: str | None = None,
                      c: Ctx = Depends(ctx(*ANY_READER))):
        sql, args = (
            "SELECT p.*, (SELECT count(*) FROM tasks t WHERE t.project_id = p.id"
            " AND t.archived_at IS NULL) AS open_tasks FROM projects p WHERE 1=1", []
        )
        if workspace:
            sql, args = sql + " AND p.workspace = ?", [*args, workspace]
        if lifecycle:
            sql, args = sql + " AND p.lifecycle = ?", [*args, lifecycle]
        rows = c.conn.execute(sql + " ORDER BY p.key", args).fetchall()
        started = projects.start_dates(c.conn, tz)
        active = projects.last_activity(c.conn)
        time_all = {p["project_id"]: p for p in
                    heartbeats.coding_summary(c.conn, 0, time.time())["projects"]}
        empty = {"editor_seconds": 0, "agent_seconds": 0, "any_seconds": 0}
        out = []
        for r in rows:
            coding = time_all.get(r["id"], empty)
            out.append({
                **dict(r), **started[r["id"]],
                "last_activity_at": active.get(r["id"]),
                "editor_seconds": coding["editor_seconds"],
                "agent_seconds": coding["agent_seconds"],
                "any_seconds": coding["any_seconds"],
            })
        return {"projects": out, "rule_version": heartbeats.DURATION_RULE}

    @app.post("/v1/projects", status_code=201)
    def create_project(body: ProjectIn, c: Ctx = Depends(ctx(*ANY_READER))):
        with tx(c.conn):
            row = projects.create_manual(c.conn, config, c.actor, name=body.name,
                                         workspace=body.workspace, lifecycle=body.lifecycle)
        return {"project": dict(row)}

    @app.get("/v1/suggestions")
    def list_suggestions(c: Ctx = Depends(ctx("user"))):
        return {"suggestions": projects.open_suggestions(c.conn)}

    @app.post("/v1/suggestions/{suggestion_id}")
    def decide_suggestion(suggestion_id: int, body: SuggestionDecision,
                          c: Ctx = Depends(ctx("user"))):
        with tx(c.conn):
            result = projects.resolve_suggestion(c.conn, c.actor, suggestion_id, body.accept)
        return result

    @app.post("/v1/projects/{source:path}/merge")
    def merge_project(source: str, body: MergeIn, c: Ctx = Depends(ctx("user"))):
        src, dst = project_or_404(c.conn, source), project_or_404(c.conn, body.into)
        if src["id"] == dst["id"]:
            raise DomainError(409, "same_project", "source and target are the same project")
        with tx(c.conn):
            projects.merge(c.conn, c.actor, src, dst)
        heartbeats.derive_pending(c.conn, config, include_unmatched=True)
        return {"merged": src["key"], "into": dict(projects.get(c.conn, dst["id"]))}

    @app.get("/v1/projects/{ref:path}/board")
    def project_board(ref: str, include_archived: bool = False,
                      c: Ctx = Depends(ctx(*ANY_READER))):
        """Everything the TUI shows for one project, in one call."""
        project = project_or_404(c.conn, ref)
        task_rows = tasks.list_tasks(c.conn, project=project["id"],
                                     include_archived=include_archived)
        lines = tasks.timelines(c.conn, [t["id"] for t in task_rows])
        for t in task_rows:
            t.update({k: v for k, v in lines[t["id"]].items() if k != "column_history"})
        columns = {col: [t for t in task_rows if t["column_name"] == col]
                   for col in config.task_columns}
        coding = {}
        for label, days in (("today", 1), ("week", 7), ("all", 36500)):
            _, start, end = window(None, days)
            summary = heartbeats.coding_summary(c.conn, start, end)
            coding[label] = next(
                (p for p in summary["projects"] if p["project_id"] == project["id"]),
                {"editor_seconds": 0, "agent_seconds": 0, "any_seconds": 0},
            )
        return {
            "project": {**dict(project), **projects.start_dates(c.conn, tz)[project["id"]]},
            "idea": ideas.for_project(c.conn, project["id"]),
            "columns": columns,
            "coding": coding,
            "commits": commits.for_project(c.conn, project["id"], limit=20),
            "rule_version": heartbeats.DURATION_RULE,
        }

    @app.patch("/v1/projects/{ref:path}")
    def patch_project(ref: str, body: ProjectPatch, c: Ctx = Depends(ctx(*ANY_READER))):
        project = project_or_404(c.conn, ref)
        if body.lifecycle is None and body.description is None:
            raise DomainError(422, "nothing_to_change", "give lifecycle and/or description")
        with tx(c.conn):
            row = project
            if body.description is not None:
                row = projects.set_description(c.conn, c.actor, project["id"], body.description)
            if body.lifecycle is not None:
                row = projects.set_lifecycle(c.conn, c.actor, project["id"], body.lifecycle)
        return {"project": dict(row)}

    @app.get("/v1/projects/{ref:path}")
    def get_project(ref: str, c: Ctx = Depends(ctx(*ANY_READER))):
        project = project_or_404(c.conn, ref)
        locations = c.conn.execute(
            "SELECT device_id, path, has_git, newest_mtime, last_seen, gone_at"
            " FROM project_locations WHERE project_id = ? ORDER BY device_id, path",
            (project["id"],),
        ).fetchall()
        aliases = c.conn.execute(
            "SELECT alias_key, reason, created_at FROM project_aliases WHERE project_id = ?",
            (project["id"],),
        ).fetchall()
        return {
            "project": {**dict(project), **projects.start_dates(c.conn, tz)[project["id"]]},
            "idea": ideas.for_project(c.conn, project["id"]),
            "locations": [dict(r) for r in locations],
            "aliases": [dict(r) for r in aliases],
            "commits": commits.for_project(c.conn, project["id"], limit=20),
        }

    # --- ideas ---------------------------------------------------------------------------

    @app.get("/v1/ideas")
    def list_ideas(status: str | None = "open", workspace: str | None = None,
                   c: Ctx = Depends(ctx(*ANY_READER))):
        # status=all lists every idea whatever its status
        return {"ideas": ideas.list_ideas(c.conn, status=None if status == "all" else status,
                                          workspace=workspace)}

    @app.post("/v1/ideas", status_code=201)
    def create_idea(body: IdeaIn, c: Ctx = Depends(ctx(*ANY_READER))):
        with tx(c.conn):
            row = ideas.create(c.conn, config, c.actor, title=body.title,
                               workspace=body.workspace, summary=body.summary, body=body.body)
        return {"idea": dict(row)}

    @app.get("/v1/ideas/{idea_id}")
    def get_idea(idea_id: str, c: Ctx = Depends(ctx(*ANY_READER))):
        return ideas.detail(c.conn, idea_id)

    @app.patch("/v1/ideas/{idea_id}")
    def patch_idea(idea_id: str, body: IdeaPatch, c: Ctx = Depends(ctx(*ANY_READER))):
        with tx(c.conn):
            row = ideas.update(c.conn, config, c.actor, idea_id, **body.model_dump())
        return {"idea": dict(row)}

    @app.post("/v1/ideas/{idea_id}/drop")
    def drop_idea(idea_id: str, c: Ctx = Depends(ctx(*ANY_READER))):
        with tx(c.conn):
            row = ideas.set_dropped(c.conn, c.actor, idea_id, True)
        return {"idea": dict(row)}

    @app.post("/v1/ideas/{idea_id}/reopen")
    def reopen_idea(idea_id: str, c: Ctx = Depends(ctx(*ANY_READER))):
        with tx(c.conn):
            row = ideas.set_dropped(c.conn, c.actor, idea_id, False)
        return {"idea": dict(row)}

    @app.post("/v1/ideas/{idea_id}/start")
    def start_idea(idea_id: str, body: IdeaStart, c: Ctx = Depends(ctx(*ANY_READER))):
        with tx(c.conn):
            row = ideas.start(c.conn, c.actor, idea_id, body.project)
        return {"idea": dict(row), "project": dict(projects.get(c.conn, row["project_id"]))}

    # --- tasks ---------------------------------------------------------------------------

    @app.get("/v1/tasks")
    def list_tasks(project: str | None = None, column: str | None = None,
                   include_archived: bool = False, c: Ctx = Depends(ctx(*ANY_READER))):
        return {"tasks": tasks.list_tasks(c.conn, project=project, column=column,
                                          include_archived=include_archived)}

    @app.post("/v1/tasks", status_code=201)
    def create_task(body: TaskIn, c: Ctx = Depends(ctx(*ANY_READER))):
        with tx(c.conn):
            row = tasks.create(c.conn, config, c.actor, project=body.project, title=body.title,
                               description=body.description, column=body.column)
        return {"task": dict(row)}

    @app.get("/v1/tasks/{task_id}")
    def get_task(task_id: str, c: Ctx = Depends(ctx(*ANY_READER))):
        return tasks.detail(c.conn, task_id)

    @app.patch("/v1/tasks/{task_id}")
    def patch_task(task_id: str, body: TaskPatch, c: Ctx = Depends(ctx(*ANY_READER))):
        with tx(c.conn):
            row = tasks.update(c.conn, c.actor, task_id, **body.model_dump())
        return {"task": dict(row)}

    @app.post("/v1/tasks/{task_id}/move")
    def move_task(task_id: str, body: MoveIn, c: Ctx = Depends(ctx(*ANY_READER))):
        with tx(c.conn):
            row = tasks.move(c.conn, config, c.actor, task_id, body.column)
        return {"task": dict(row)}

    @app.post("/v1/tasks/{task_id}/notes", status_code=201)
    def add_note(task_id: str, body: NoteIn, c: Ctx = Depends(ctx(*ANY_READER))):
        with tx(c.conn):
            note = tasks.add_note(c.conn, c.actor, task_id, body.body)
        return {"note": note}

    @app.post("/v1/tasks/{task_id}/archive")
    def archive_task(task_id: str, c: Ctx = Depends(ctx(*ANY_READER))):
        with tx(c.conn):
            row = tasks.set_archived(c.conn, c.actor, task_id, True)
        return {"task": dict(row)}

    @app.post("/v1/tasks/{task_id}/unarchive")
    def unarchive_task(task_id: str, c: Ctx = Depends(ctx(*ANY_READER))):
        with tx(c.conn):
            row = tasks.set_archived(c.conn, c.actor, task_id, False)
        return {"task": dict(row)}

    # --- selection & sessions ------------------------------------------------------------

    @app.get("/v1/selection")
    def get_selection(device: str | None = None,
                      c: Ctx = Depends(ctx("user", "agent", "collector"))):
        return {"selections": selection.current(c.conn, device)}

    @app.put("/v1/selection")
    def put_selection(body: SelectIn, c: Ctx = Depends(ctx(*ANY_READER))):
        with tx(c.conn):
            current = selection.select(c.conn, c.actor, c.principal.device_id,
                                       task=body.task, project=body.project)
        return {"selection": current}

    @app.delete("/v1/selection")
    def clear_selection(reason: str = "clear",
                        c: Ctx = Depends(ctx("user", "agent", "collector"))):
        with tx(c.conn):
            current = selection.clear(c.conn, c.actor, c.principal.device_id, reason)
        return {"selection": current}

    @app.put("/v1/sessions/{harness}/{session_id}/task")
    def link_session(harness: str, session_id: str, body: SessionLinkIn,
                     c: Ctx = Depends(ctx(*ANY_READER))):
        with tx(c.conn):
            return {"link": selection.link_session(
                c.conn, c.actor, harness=harness, session_id=session_id, task=body.task)}

    # --- coding activity -------------------------------------------------------------------

    @app.post("/v1/imports/wakapi")
    async def import_now(from_start: bool = False, _c: Ctx = Depends(ctx("user"))):
        if not config.wakapi.api_key:
            raise DomainError(409, "not_configured", "MNEMOSYNE_WAKAPI_API_KEY is not set")
        try:
            return await asyncio.to_thread(run_wakapi_import, from_start)
        except wakapi.WakapiError as exc:
            raise DomainError(502, "wakapi_error", str(exc)) from exc

    @app.get("/v1/coding/summary")
    def coding_summary(day: str | None = None, days: int = 1,
                       c: Ctx = Depends(ctx(*ANY_READER))):
        d, start, end = window(day, max(1, days))
        return {"date": d.isoformat(), "days": max(1, days), "timezone": config.timezone,
                **heartbeats.coding_summary(c.conn, start, end)}

    @app.get("/v1/heartbeats")
    def list_heartbeats(day: str | None = None, project: str | None = None, limit: int = 500,
                        c: Ctx = Depends(ctx("user"))):
        d, start, end = window(day, 1)
        sql = (
            "SELECT h.id, h.source_id, h.time, h.machine_name, h.project_name, h.entity,"
            " h.category, h.user_agent, d.device_id, d.origin, d.plugin, d.project_id,"
            " d.match_rule, d.rule_version FROM raw_heartbeats h"
            " LEFT JOIN heartbeat_derivations d ON d.heartbeat_id = h.id"
            " WHERE h.time >= ? AND h.time < ?"
        )
        args: list = [start, end]
        if project:
            found = project_or_404(c.conn, project)
            sql, args = sql + " AND d.project_id = ?", [*args, found["id"]]
        rows = c.conn.execute(sql + " ORDER BY h.time LIMIT ?", [*args, limit]).fetchall()
        return {"date": d.isoformat(), "heartbeats": [dict(r) for r in rows]}

    return app
