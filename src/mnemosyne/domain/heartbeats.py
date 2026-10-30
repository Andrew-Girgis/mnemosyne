"""Derivations over raw Wakapi heartbeats: origin, device, project, durations.

Every derived value records the rule version so totals can be reproduced.
"""

import re
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass
from itertools import pairwise

from mnemosyne.config import Config
from mnemosyne.domain import projects

ORIGIN_RULE = "origin-v2"
MAPPING_RULE = "project-map-v3"
DURATION_RULE = "coding-duration-v1"

# origin-v2: a heartbeat is agent evidence if WakaTime categorised it as 'ai coding'
# (every agent-harness plugin seen so far does, whatever its user agent says) or its
# plugin names a known agent; anything else (editor/IDE plugins) is human evidence.
AI_CATEGORY = "ai coding"
AGENT_PLUGIN_MARKERS = (
    "claude-code",
    "opencode",
    "pi-coding-agent",
    "pi-wakatime",
    "codex",
    "hermes agent",
)

# project-map-v2: when a test invocation flushed one machine's offline queue (26 Sep 2026),
# wakatime-cli appended the test plugin label to every flushed heartbeat and filled the
# missing project/branch of 531 of them with the invoking repo ('mnemosyne'/'main').
# Only that filled-in name is untrusted; those heartbeats match by entity path alone.
# project-map-v3: a flushed heartbeat that had no entity got the invoking directory
# itself ('~/Projects/mnemosyne', a directory, never a file anyone edited) as its entity,
# which dated mnemosyne back to April. That filled-in entity is untrusted too.
UNTRUSTED_STAMPS = {("mnemosyne-m1-check/", "mnemosyne")}
UNTRUSTED_STAMPED_ENTITIES = {("mnemosyne-m1-check/", "/home/girgie/Projects/mnemosyne")}

# coding-duration-v1: a heartbeat counts until the next one in the same (device, origin)
# stream, capped at Wakapi's default heartbeat timeout; the last one in a stream counts 0.
HEARTBEAT_TIMEOUT_SECONDS = 600

_GO_VERSION = re.compile(r"\bgo\d[\w.]*\s*")


def plugin_of(user_agent: str | None) -> str | None:
    """'wakatime/v1.90 (linux-…) go1.22 opencode-cli/1.15 opencode-wakatime/1.3' →
    'opencode-cli/1.15 opencode-wakatime/1.3'."""
    if not user_agent:
        return None
    tail = user_agent.rsplit(")", 1)[-1]
    tail = _GO_VERSION.sub("", tail).strip()
    return tail or user_agent.strip()


def origin_of(user_agent: str | None, category: str | None = None) -> str:
    if (category or "").lower() == AI_CATEGORY:
        return "agent"
    lowered = (user_agent or "").lower()
    return "agent" if any(m in lowered for m in AGENT_PLUGIN_MARKERS) else "editor"


def derive(conn: sqlite3.Connection, config: Config, heartbeat: sqlite3.Row) -> dict:
    device = config.device_for_machine(heartbeat["machine_name"])
    device_id = device.id if device else None
    project, rule = None, "unmatched"
    entity = heartbeat["entity"] or ""
    user_agent = heartbeat["user_agent"] or ""
    filled_entity = any(marker in user_agent and entity.rstrip("/") == path
                        for marker, path in UNTRUSTED_STAMPED_ENTITIES)
    if entity.startswith("/") and not filled_entity:
        candidates = [device_id] if device_id else list(config.devices)
        for candidate in candidates:
            project, rule = projects.project_for_path(conn, config, candidate, entity)
            if project:
                break
    stamped = filled_entity or any(
        marker in user_agent and heartbeat["project_name"] == name
        for marker, name in UNTRUSTED_STAMPS
    )
    if project is None and heartbeat["project_name"] and not stamped:
        named = conn.execute(
            "SELECT * FROM projects WHERE name = ?", (heartbeat["project_name"],)
        ).fetchall()
        if len(named) == 1:
            project, rule = named[0], "name"
    if project is None and stamped:
        rule = "unmatched-stamped"
    row = {
        "heartbeat_id": heartbeat["id"],
        "device_id": device_id,
        "origin": origin_of(heartbeat["user_agent"], heartbeat["category"]),
        "plugin": plugin_of(heartbeat["user_agent"]),
        "project_id": project["id"] if project else None,
        "match_rule": rule,
        "rule_version": f"{ORIGIN_RULE}+{MAPPING_RULE}",
        "derived_at": time.time(),
    }
    conn.execute(
        "INSERT INTO heartbeat_derivations (heartbeat_id, device_id, origin, plugin, project_id,"
        " match_rule, rule_version, derived_at) VALUES (:heartbeat_id, :device_id, :origin,"
        " :plugin, :project_id, :match_rule, :rule_version, :derived_at)"
        " ON CONFLICT (heartbeat_id) DO UPDATE SET device_id = excluded.device_id,"
        " origin = excluded.origin, plugin = excluded.plugin, project_id = excluded.project_id,"
        " match_rule = excluded.match_rule, rule_version = excluded.rule_version,"
        " derived_at = excluded.derived_at",
        row,
    )
    return row


def derive_outdated(conn: sqlite3.Connection, config: Config) -> int:
    """Re-derive every heartbeat whose derivation predates the current rule versions."""
    rows = conn.execute(
        "SELECT h.* FROM raw_heartbeats h JOIN heartbeat_derivations d"
        " ON d.heartbeat_id = h.id WHERE d.rule_version != ?",
        (f"{ORIGIN_RULE}+{MAPPING_RULE}",),
    ).fetchall()
    for heartbeat in rows:
        derive(conn, config, heartbeat)
    return len(rows)


def derive_pending(conn: sqlite3.Connection, config: Config, *, include_unmatched: bool) -> int:
    """Derive heartbeats that have no derivation yet (and optionally retry unmatched ones,
    e.g. after a scan registered new projects)."""
    where = "d.heartbeat_id IS NULL"
    if include_unmatched:
        where += (
            " OR d.match_rule IN ('unmatched', 'unmatched-stamped', 'name', 'graduated-name')"
        )
    rows = conn.execute(
        f"SELECT h.* FROM raw_heartbeats h LEFT JOIN heartbeat_derivations d"
        f" ON d.heartbeat_id = h.id WHERE {where}"
    ).fetchall()
    for heartbeat in rows:
        derive(conn, config, heartbeat)
    return len(rows)


@dataclass(frozen=True)
class Interval:
    start: float
    end: float
    device_id: str | None
    origin: str
    project_id: str | None


def coding_intervals(conn: sqlite3.Connection, start: float, end: float) -> list[Interval]:
    """coding-duration-v1 intervals for heartbeats in [start, end), clipped to the window."""
    rows = conn.execute(
        "SELECT h.time, d.device_id, d.origin, d.project_id FROM raw_heartbeats h"
        " JOIN heartbeat_derivations d ON d.heartbeat_id = h.id"
        " WHERE h.time >= ? AND h.time < ? ORDER BY h.time",
        (start - HEARTBEAT_TIMEOUT_SECONDS, end),
    ).fetchall()
    streams: dict[tuple, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        streams[(row["device_id"], row["origin"])].append(row)
    intervals = []
    for (device_id, origin), beats in streams.items():
        for current, following in pairwise(beats):
            gap = following["time"] - current["time"]
            if 0 < gap <= HEARTBEAT_TIMEOUT_SECONDS:
                lo, hi = max(current["time"], start), min(following["time"], end)
                if hi > lo:
                    intervals.append(Interval(lo, hi, device_id, origin, current["project_id"]))
    return intervals


def union_seconds(spans: list[tuple[float, float]]) -> float:
    total, cur_start, cur_end = 0.0, None, None
    for lo, hi in sorted(spans):
        if cur_end is None or lo > cur_end:
            if cur_end is not None:
                total += cur_end - cur_start
            cur_start, cur_end = lo, hi
        else:
            cur_end = max(cur_end, hi)
    if cur_end is not None:
        total += cur_end - cur_start
    return total


def coding_summary(conn: sqlite3.Connection, start: float, end: float) -> dict:
    """Per-project coding time split by origin. Overlapping intervals (two devices, two
    agent sessions) are unioned within each (project, origin), never summed."""
    by_key: dict[tuple, list[tuple[float, float]]] = defaultdict(list)
    for iv in coding_intervals(conn, start, end):
        by_key[(iv.project_id, iv.origin)].append((iv.start, iv.end))
        by_key[(iv.project_id, "any")].append((iv.start, iv.end))
        by_key[("__all__", iv.origin)].append((iv.start, iv.end))
        by_key[("__all__", "any")].append((iv.start, iv.end))
    project_ids = {pid for pid, _ in by_key if pid != "__all__"}
    known = [p for p in project_ids if p]
    names = {
        row["id"]: row
        for row in conn.execute(
            f"SELECT id, key, name, workspace FROM projects WHERE id IN"
            f" ({','.join('?' * len(known))})",
            known,
        )
    } if known else {}
    items = []
    for pid in sorted(project_ids, key=lambda p: (p is None, p or "")):
        project = names.get(pid)
        items.append({
            "project_id": pid,
            "project_key": project["key"] if project else None,
            "workspace": project["workspace"] if project else None,
            "editor_seconds": round(union_seconds(by_key.get((pid, "editor"), []))),
            "agent_seconds": round(union_seconds(by_key.get((pid, "agent"), []))),
            "any_seconds": round(union_seconds(by_key.get((pid, "any"), []))),
        })
    items.sort(key=lambda i: -i["any_seconds"])
    return {
        "rule_version": DURATION_RULE,
        "totals": {
            "editor_seconds": round(union_seconds(by_key.get(("__all__", "editor"), []))),
            "agent_seconds": round(union_seconds(by_key.get(("__all__", "agent"), []))),
            "any_seconds": round(union_seconds(by_key.get(("__all__", "any"), []))),
        },
        "projects": items,
    }
