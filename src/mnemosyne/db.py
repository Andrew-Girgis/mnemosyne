"""SQLite access and ordered SQL migrations."""

import sqlite3
import time
from importlib import resources
from pathlib import Path


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _migration_files() -> list[tuple[int, str, str]]:
    files = []
    for entry in resources.files("mnemosyne.migrations").iterdir():
        if entry.name.endswith(".sql"):
            version = int(entry.name.split("_", 1)[0])
            files.append((version, entry.name, entry.read_text()))
    return sorted(files)


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Apply pending migrations in order; returns the names applied."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at REAL NOT NULL)"
    )
    done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    applied = []
    for version, name, sql in _migration_files():
        if version in done:
            continue
        conn.execute("BEGIN")
        try:
            for statement in _statements(sql):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations VALUES (?, ?, ?)", (version, name, time.time())
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        applied.append(name)
    return applied


def _statements(sql: str) -> list[str]:
    # executescript() would commit implicitly; split so each migration is one transaction.
    # complete_statement() understands comments and string literals containing ';'.
    statements, buffer = [], ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            if buffer.strip():
                statements.append(buffer.strip())
            buffer = ""
    if buffer.strip() and not all(
        ln.strip().startswith("--") or not ln.strip() for ln in buffer.splitlines()
    ):
        raise ValueError(f"incomplete SQL statement at end of migration: {buffer[:80]!r}")
    return statements
