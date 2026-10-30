"""Commit evidence from device scans. Commits never drive time totals."""

import sqlite3
import time

from mnemosyne.config import Config
from mnemosyne.domain import projects

RULE = "commit-map-v1"


def project_for_repo(conn: sqlite3.Connection, config: Config, device_id: str, repo: dict):
    """A working copy maps by path; a bare mirror '<name>.git' maps to the Try of that name,
    else the graduated project of that name, in the mirror's workspace."""
    if repo.get("mirror_workspace"):
        ws = config.workspaces.get(repo["mirror_workspace"])
        if ws is None:
            return None
        name = repo["name"]
        return (projects.resolve_key(conn, f"{ws.name}/{projects._try_rel_root(ws)}/{name}")
                or projects.resolve_key(conn, f"{ws.name}/{name}"))
    project, _ = projects.project_for_path(conn, config, device_id, repo["path"])
    return project


def ingest(conn: sqlite3.Connection, config: Config, device_id: str, repos: list[dict]) -> dict:
    now = time.time()
    new_commits = linked = 0
    for repo in repos:
        project = project_for_repo(conn, config, device_id, repo)
        for c in repo.get("commits", []):
            cur = conn.execute(
                "INSERT OR IGNORE INTO raw_commits (sha, root_commit, authored_at, committed_at,"
                " author_email, subject, first_device, first_path, observed_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (c["sha"], repo.get("root_commit"), c["authored_at"], c["committed_at"],
                 c.get("author_email"), c.get("subject"), device_id, repo["path"], now),
            )
            new_commits += cur.rowcount
            if project is not None:
                linked += conn.execute(
                    "INSERT OR IGNORE INTO commit_projects (sha, project_id, rule_version)"
                    " VALUES (?, ?, ?)",
                    (c["sha"], project["id"], RULE),
                ).rowcount
    return {"repos": len(repos), "new_commits": new_commits, "new_links": linked}


def for_project(conn: sqlite3.Connection, project_id: str, limit: int = 50) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT c.sha, c.committed_at, c.author_email, c.subject FROM commit_projects cp"
        " JOIN raw_commits c ON c.sha = cp.sha WHERE cp.project_id = ?"
        " ORDER BY c.committed_at DESC LIMIT ?",
        (project_id, limit),
    )]
