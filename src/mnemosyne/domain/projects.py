"""Project registry: device scans in, stable projects out.

A project's key is device-independent: '<workspace>/<path under the workspace base>',
e.g. 'personal/tries/2026-09-25-mnemosyne' or 'personal/mnemosyne' once graduated.
Moves are followed when the evidence is unambiguous; otherwise a suggestion is stored.
"""

import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from mnemosyne import ids
from mnemosyne.config import Config
from mnemosyne.domain import audit
from mnemosyne.domain.audit import Actor

_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}-")
_TRY_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})-")


@dataclass(frozen=True)
class ScanEntry:
    workspace: str
    relpath: str  # relative to the workspace base, e.g. 'tries/2026-…' or 'Argus'
    path: str  # absolute on the scanning device
    kind: str  # 'try' | 'graduated'
    has_git: bool
    git_root_commit: str | None
    newest_mtime: float | None


@dataclass
class ScanResult:
    created: list[str]
    moved: list[dict]
    suggested: list[dict]
    located: int
    gone: int
    links: list[dict] = field(default_factory=list)


def bare_name(name: str) -> str:
    return _DATE_PREFIX.sub("", name)


def resolve_key(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    row = conn.execute("SELECT * FROM projects WHERE key = ?", (key,)).fetchone()
    if row:
        return row
    return conn.execute(
        "SELECT p.* FROM project_aliases a JOIN projects p ON p.id = a.project_id"
        " WHERE a.alias_key = ?",
        (key,),
    ).fetchone()


def get(conn: sqlite3.Connection, id_or_key: str) -> sqlite3.Row | None:
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (id_or_key,)).fetchone()
    return row or resolve_key(conn, id_or_key)


def create(
    conn: sqlite3.Connection,
    actor: Actor,
    *,
    key: str,
    workspace: str,
    name: str,
    kind: str,
    lifecycle: str,
    git_root_commit: str | None = None,
) -> sqlite3.Row:
    now = time.time()
    project_id = ids.new_id("prj")
    conn.execute(
        "INSERT INTO projects (id, key, workspace, name, kind, lifecycle, git_root_commit,"
        " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (project_id, key, workspace, name, kind, lifecycle, git_root_commit, now, now),
    )
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    audit.record(conn, actor, "project.create", "project", project_id, after=dict(row))
    return row


def ingest_scan(
    conn: sqlite3.Connection,
    config: Config,
    device_id: str,
    entries: list[ScanEntry],
    complete: bool,
    actor: Actor,
    links: list[dict] | None = None,
) -> ScanResult:
    """Apply one device's scan. `complete` means every configured root was fully listed,
    so locations missing from it are gone (which is what makes move detection possible)."""
    now = time.time()
    result = ScanResult([], [], [], 0, 0)
    current_paths = {e.path for e in entries}
    conn.execute("BEGIN IMMEDIATE")
    try:
        for entry in entries:
            key = f"{entry.workspace}/{entry.relpath}"
            project = resolve_key(conn, key)
            if project is None and complete:
                # Only a complete scan can prove a location vanished, which moves rely on.
                project = _follow_move(conn, device_id, entry, key, current_paths, actor, result)
            if project is None:
                active_since = now - config.seed_active_days * 86400
                lifecycle = (
                    "active"
                    if entry.newest_mtime and entry.newest_mtime >= active_since
                    else "paused"
                )
                project = create(
                    conn,
                    actor,
                    key=key,
                    workspace=entry.workspace,
                    name=entry.relpath.rsplit("/", 1)[-1],
                    kind=entry.kind,
                    lifecycle=lifecycle,
                    git_root_commit=entry.git_root_commit,
                )
                result.created.append(project["id"])
                if complete:
                    _suggest_moves(conn, device_id, entry, project, current_paths, result)
            elif entry.git_root_commit and not project["git_root_commit"]:
                conn.execute(
                    "UPDATE projects SET git_root_commit = ?, updated_at = ? WHERE id = ?",
                    (entry.git_root_commit, now, project["id"]),
                )
            conn.execute(
                "INSERT INTO project_locations (device_id, path, project_id, has_git,"
                " newest_mtime, first_seen, last_seen, gone_at) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)"
                " ON CONFLICT (device_id, path) DO UPDATE SET project_id = excluded.project_id,"
                " has_git = excluded.has_git, newest_mtime = excluded.newest_mtime,"
                " last_seen = excluded.last_seen, gone_at = NULL",
                (device_id, entry.path, project["id"], int(entry.has_git), entry.newest_mtime,
                 now, now),
            )
            result.located += 1
        result.links = ingest_links(conn, config, device_id, links or [], actor)
        if complete:
            stale = [
                row["path"]
                for row in conn.execute(
                    "SELECT path FROM project_locations WHERE device_id = ? AND gone_at IS NULL",
                    (device_id,),
                )
                if row["path"] not in current_paths
            ]
            for path in stale:
                conn.execute(
                    "UPDATE project_locations SET gone_at = ? WHERE device_id = ? AND path = ?",
                    (now, device_id, path),
                )
            result.gone = len(stale)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return result


def _vanished_candidates(
    conn: sqlite3.Connection, device_id: str, current_paths: set[str]
) -> list[sqlite3.Row]:
    """Projects that had a live location on this device which the current scan no longer sees."""
    rows = conn.execute(
        "SELECT p.*, l.path AS old_path FROM project_locations l JOIN projects p"
        " ON p.id = l.project_id WHERE l.device_id = ? AND l.gone_at IS NULL",
        (device_id,),
    ).fetchall()
    return [r for r in rows if r["old_path"] not in current_paths]


def _follow_move(conn, device_id, entry, key, current_paths, actor, result):
    candidates = _vanished_candidates(conn, device_id, current_paths)
    by_commit = [
        c for c in candidates
        if entry.git_root_commit and c["git_root_commit"] == entry.git_root_commit
    ]
    # Name evidence alone only follows graduation (try → graduated); other renames get a
    # suggestion, because unrelated tries often share a name once the date is stripped.
    by_name = [
        c for c in candidates
        if entry.kind == "graduated" and c["kind"] == "try"
        and c["workspace"] == entry.workspace
        and bare_name(c["name"]) == bare_name(entry.relpath.rsplit("/", 1)[-1])
    ]
    rule, matches = ("root-commit", by_commit) if by_commit else ("name", by_name)
    if len({m["id"] for m in matches}) != 1:
        return None
    old = matches[0]
    now = time.time()
    conn.execute(
        "INSERT OR IGNORE INTO project_aliases (alias_key, project_id, reason, created_at)"
        " VALUES (?, ?, ?, ?)",
        (old["key"], old["id"], f"moved:{rule}", now),
    )
    conn.execute(
        "UPDATE projects SET key = ?, name = ?, kind = ?, git_root_commit ="
        " COALESCE(git_root_commit, ?), updated_at = ? WHERE id = ?",
        (key, entry.relpath.rsplit("/", 1)[-1], entry.kind, entry.git_root_commit, now, old["id"]),
    )
    conn.execute(
        "UPDATE project_locations SET gone_at = ? WHERE device_id = ? AND path = ?",
        (now, device_id, old["old_path"]),
    )
    project = conn.execute("SELECT * FROM projects WHERE id = ?", (old["id"],)).fetchone()
    audit.record(
        conn, actor, "project.move", "project", old["id"],
        before={"key": old["key"], "path": old["old_path"]},
        after={"key": key, "path": entry.path, "rule": rule},
    )
    result.moved.append({"project_id": old["id"], "from": old["key"], "to": key, "rule": rule})
    return project


def _suggest_moves(conn, device_id, entry, project, current_paths, result):
    """Several vanished projects could be this one: ask the user instead of guessing."""
    name = bare_name(project["name"])
    for c in _vanished_candidates(conn, device_id, current_paths):
        same_commit = entry.git_root_commit and c["git_root_commit"] == entry.git_root_commit
        if c["id"] != project["id"] and (same_commit or bare_name(c["name"]) == name):
            evidence = "root-commit" if same_commit else "name"
            conn.execute(
                "INSERT INTO move_suggestions (from_project_id, to_project_id, evidence,"
                " created_at) VALUES (?, ?, ?, ?)",
                (c["id"], project["id"], evidence, time.time()),
            )
            result.suggested.append({"from": c["key"], "to": project["key"], "evidence": evidence})


def _first_component_under(device, path: str, root: str) -> str | None:
    """The first path component below `root`, or None if `path` is not under it.
    macOS paths are case-insensitive (heartbeats may say both 'Work' and 'WORK')."""
    prefix = device.expand(root).rstrip("/") + "/"
    matches = (path.casefold().startswith(prefix.casefold()) if device.os == "macos"
               else path.startswith(prefix))
    if not matches:
        return None
    return path[len(prefix):].split("/", 1)[0] or None


def key_for_path(config: Config, device_id: str, path: str) -> str | None:
    """Map an absolute path on a device to the project key it would belong to."""
    device = config.devices.get(device_id)
    if device is None:
        return None
    for ws in config.workspaces.values():
        name = _first_component_under(device, path, ws.try_root)
        if name:
            return f"{ws.name}/{_try_rel_root(ws)}/{name}"
    for ws in config.workspaces.values():
        name = _first_component_under(device, path, ws.base)
        if name:
            return f"{ws.name}/{name}"
    return None


def legacy_name_for_path(config: Config, device_id: str, path: str) -> str | None:
    for root in config.legacy_roots:
        if root.device == device_id:
            name = _first_component_under(config.devices[device_id], path, root.path)
            if name:
                return name
    return None


def resolve_legacy(conn: sqlite3.Connection, config: Config, name: str) -> sqlite3.Row:
    """A legacy '~/src/tries/<name>' maps to the matching Try project, else a legacy project."""
    for ws in config.workspaces.values():
        rel_root = _try_rel_root(ws)
        project = resolve_key(conn, f"{ws.name}/{rel_root}/{name}")
        if project:
            return project
    key = f"legacy/{name}"
    project = resolve_key(conn, key)
    if project:
        return project
    return create(
        conn, audit.SYSTEM, key=key, workspace="legacy", name=name, kind="legacy",
        lifecycle="paused",
    )


def _try_rel_root(ws) -> str:
    base = ws.base.rstrip("/") + "/"
    return ws.try_root[len(base):].rstrip("/") if ws.try_root.startswith(base) else "tries"


def project_for_path(
    conn: sqlite3.Connection, config: Config, device_id: str, path: str
) -> tuple[sqlite3.Row | None, str]:
    """Resolve an absolute path on a device to (project, match_rule)."""
    key = key_for_path(config, device_id, path)
    if key:
        project = resolve_key(conn, key)
        if project:
            return project, "path"
        # A try that graduated before Mnemosyne first scanned it has no alias yet.
        workspace, _, rest = key.partition("/")
        if "/" in rest:
            name = bare_name(rest.rsplit("/", 1)[-1])
            graduated = conn.execute(
                "SELECT * FROM projects WHERE workspace = ? AND kind = 'graduated' AND name = ?",
                (workspace, name),
            ).fetchall()
            if len(graduated) == 1:
                return graduated[0], "graduated-name"
        return None, "unmatched"
    legacy = legacy_name_for_path(config, device_id, path)
    if legacy:
        return resolve_legacy(conn, config, legacy), "legacy-path"
    return None, "unmatched"


def merge(
    conn: sqlite3.Connection, actor: Actor, source: sqlite3.Row, target: sqlite3.Row,
    reason: str = "merge",
) -> None:
    """Fold `source` into `target`: every reference moves, the source key becomes an alias,
    and the source row is removed (its full prior state is kept in the audit log).
    The caller owns the transaction."""
    if source["id"] == target["id"]:
        raise ValueError("cannot merge a project into itself")
    now = time.time()
    moves = [
        ("project_locations", "project_id"),
        ("tasks", "project_id"),
        ("heartbeat_derivations", "project_id"),
        ("selection_events", "project_id"),
        ("project_aliases", "project_id"),
        ("move_suggestions", "from_project_id"),
        ("move_suggestions", "to_project_id"),
        ("ideas", "project_id"),
    ]
    for table, column in moves:
        conn.execute(
            f"UPDATE {table} SET {column} = ? WHERE {column} = ?", (target["id"], source["id"])
        )
    conn.execute(
        "DELETE FROM move_suggestions WHERE from_project_id = to_project_id"
        " AND resolved_at IS NULL"
    )
    conn.execute(
        "INSERT OR REPLACE INTO project_aliases (alias_key, project_id, reason, created_at)"
        " VALUES (?, ?, ?, ?)",
        (source["key"], target["id"], reason, now),
    )
    conn.execute(
        "UPDATE projects SET git_root_commit = COALESCE(git_root_commit, ?), updated_at = ?"
        " WHERE id = ?",
        (source["git_root_commit"], now, target["id"]),
    )
    conn.execute("DELETE FROM projects WHERE id = ?", (source["id"],))
    audit.record(
        conn, actor, "project.merge", "project", target["id"],
        before=dict(source), after={"into": target["id"], "key": target["key"], "reason": reason},
    )


def ingest_links(
    conn: sqlite3.Connection, config: Config, device_id: str, links: list[dict], actor: Actor
) -> list[dict]:
    """A symlink left in a Try root by graduation: its key becomes an alias of the target.
    If an earlier scan already made a project from the link, that project is merged in."""
    applied = []
    for link in links:
        key = f"{link['workspace']}/{link['relpath']}"
        target_key = key_for_path(config, device_id, link["target_path"])
        target = resolve_key(conn, target_key) if target_key else None
        if target is None:
            continue
        existing = resolve_key(conn, key)
        if existing is None:
            conn.execute(
                "INSERT INTO project_aliases (alias_key, project_id, reason, created_at)"
                " VALUES (?, ?, 'link', ?)",
                (key, target["id"], time.time()),
            )
            applied.append({"alias": key, "project_id": target["id"]})
        elif existing["id"] != target["id"]:
            merge(conn, actor, existing, target, reason="link")
            applied.append({"merged": existing["key"], "into": target["key"]})
    return applied


_SLUG = re.compile(r"[^a-z0-9._-]+")


def create_manual(
    conn: sqlite3.Connection, config: Config, actor: Actor, *, name: str, workspace: str,
    lifecycle: str = "active",
) -> sqlite3.Row:
    """A project with no folder (yet). Agents may create projects (spec)."""
    from mnemosyne.domain.errors import DomainError

    if workspace not in config.workspaces:
        raise DomainError(422, "invalid_workspace",
                          f"workspace must be one of {', '.join(config.workspaces)}")
    slug = _SLUG.sub("-", name.strip().lower()).strip("-")
    if not slug:
        raise DomainError(422, "invalid_name", "name must contain letters or digits")
    key = f"{workspace}/{slug}"
    if resolve_key(conn, key):
        raise DomainError(409, "exists", f"project {key!r} already exists")
    if lifecycle not in LIFECYCLES:
        raise DomainError(422, "invalid_lifecycle", f"lifecycle must be one of {LIFECYCLES}")
    return create(conn, actor, key=key, workspace=workspace, name=name.strip(), kind="manual",
                  lifecycle=lifecycle)


LIFECYCLES = ("active", "paused", "archived")


def set_lifecycle(conn: sqlite3.Connection, actor: Actor, project_id: str, lifecycle: str):
    from mnemosyne.domain.errors import DomainError, forbidden

    if actor.kind != "user":
        raise forbidden("only the user changes a project's lifecycle")
    if lifecycle not in LIFECYCLES:
        raise DomainError(422, "invalid_lifecycle", f"lifecycle must be one of {LIFECYCLES}")
    before = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    conn.execute(
        "UPDATE projects SET lifecycle = ?, updated_at = ? WHERE id = ?",
        (lifecycle, time.time(), project_id),
    )
    audit.record(conn, actor, "project.lifecycle", "project", project_id,
                 before={"lifecycle": before["lifecycle"]}, after={"lifecycle": lifecycle})
    return conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()


def open_suggestions(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT s.id, s.evidence, s.created_at, f.key AS from_key, f.id AS from_id,"
        " t.key AS to_key, t.id AS to_id FROM move_suggestions s"
        " JOIN projects f ON f.id = s.from_project_id JOIN projects t ON t.id = s.to_project_id"
        " WHERE s.resolved_at IS NULL ORDER BY s.created_at"
    )]


def resolve_suggestion(conn: sqlite3.Connection, actor: Actor, suggestion_id: int, accept: bool):
    """Accepting keeps the old project's ID and history under the new location's key."""
    from mnemosyne.domain.errors import forbidden, not_found

    if actor.kind != "user":
        raise forbidden("only the user resolves move suggestions")
    row = conn.execute(
        "SELECT * FROM move_suggestions WHERE id = ? AND resolved_at IS NULL", (suggestion_id,)
    ).fetchone()
    if row is None:
        raise not_found("open suggestion", str(suggestion_id))
    now = time.time()
    conn.execute(
        "UPDATE move_suggestions SET resolved_at = ?, resolution = ? WHERE id = ?",
        (now, "accepted" if accept else "rejected", suggestion_id),
    )
    if accept:
        old = conn.execute("SELECT * FROM projects WHERE id = ?",
                           (row["from_project_id"],)).fetchone()
        new = conn.execute("SELECT * FROM projects WHERE id = ?",
                           (row["to_project_id"],)).fetchone()
        merge(conn, actor, new, old, reason="moved:accepted")
        conn.execute("DELETE FROM project_aliases WHERE alias_key = ?", (new["key"],))
        conn.execute(
            "INSERT OR REPLACE INTO project_aliases (alias_key, project_id, reason, created_at)"
            " VALUES (?, ?, 'moved:accepted', ?)",
            (old["key"], old["id"], now),
        )
        conn.execute("UPDATE projects SET key = ?, name = ?, kind = ?, updated_at = ? WHERE id = ?",
                     (new["key"], new["name"], new["kind"], now, old["id"]))
    audit.record(conn, actor, "project.suggestion", "move_suggestion", str(suggestion_id),
                 after={"accepted": accept})
    return {"id": suggestion_id, "accepted": accept}


def set_description(conn: sqlite3.Connection, actor: Actor, project_id: str, description: str):
    before = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    description = description.strip()
    if before["description"] == description:
        return before
    conn.execute("UPDATE projects SET description = ?, updated_at = ? WHERE id = ?",
                 (description, time.time(), project_id))
    audit.record(conn, actor, "project.describe", "project", project_id,
                 before={"description": before["description"]},
                 after={"description": description})
    return conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()


def _try_date(name: str, tz: ZoneInfo) -> float | None:
    m = _TRY_DATE.match(name)
    if not m:
        return None
    try:
        return datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=tz).timestamp()
    except ValueError:
        return None


def start_dates(conn: sqlite3.Connection, tz: ZoneInfo) -> dict[str, dict]:
    """started-v1: when each project started, with the evidence used.

    A Try folder's date prefix (on the name or any alias, e.g. a graduated project's old
    Try key) is when the user created it, so it wins. Otherwise the earliest of the first
    matched heartbeat, the first linked commit, and when Mnemosyne first saw the project."""
    rows = conn.execute("SELECT id, name, created_at FROM projects").fetchall()
    aliases: dict[str, list[str]] = {}
    for a in conn.execute("SELECT alias_key, project_id FROM project_aliases"):
        aliases.setdefault(a["project_id"], []).append(a["alias_key"].rsplit("/", 1)[-1])
    first_beat = dict(conn.execute(
        "SELECT d.project_id, min(h.time) FROM heartbeat_derivations d"
        " JOIN raw_heartbeats h ON h.id = d.heartbeat_id WHERE d.project_id IS NOT NULL"
        " GROUP BY d.project_id"
    ).fetchall())
    first_commit = dict(conn.execute(
        "SELECT cp.project_id, min(c.committed_at) FROM commit_projects cp"
        " JOIN raw_commits c ON c.sha = cp.sha GROUP BY cp.project_id"
    ).fetchall())
    result = {}
    for p in rows:
        dated = [d for d in (_try_date(n, tz) for n in [p["name"], *aliases.get(p["id"], [])])
                 if d is not None]
        if dated:
            result[p["id"]] = {"started_at": min(dated), "started_source": "try folder"}
            continue
        evidence = [(p["created_at"], "first seen")]
        if first_beat.get(p["id"]) is not None:
            evidence.append((first_beat[p["id"]], "first heartbeat"))
        if first_commit.get(p["id"]) is not None:
            evidence.append((first_commit[p["id"]], "first commit"))
        at, source = min(evidence)
        result[p["id"]] = {"started_at": at, "started_source": source}
    return result


def last_activity(conn: sqlite3.Connection) -> dict[str, float]:
    """Most recent matched heartbeat per project (for 'recently active' ordering)."""
    return dict(conn.execute(
        "SELECT d.project_id, max(h.time) FROM heartbeat_derivations d"
        " JOIN raw_heartbeats h ON h.id = d.heartbeat_id WHERE d.project_id IS NOT NULL"
        " GROUP BY d.project_id"
    ).fetchall())
