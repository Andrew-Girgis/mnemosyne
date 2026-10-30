"""Import heartbeats from Wakapi's supported WakaTime-compatible API.

Never touches Wakapi's database. Each heartbeat's source ID is a fingerprint of its
content (not Wakapi's row ID), so reimports and a rebuilt Wakapi never double-count.
Late heartbeats are normal: WakaTime clients queue them while offline and flush them
days or months later. So every run re-reads the last `RESYNC_DAYS`, and the first run of
each day re-reads `DEEP_RESYNC_DAYS` (Wakapi's default heartbeat_max_age: it rejects
anything older on arrival); fingerprints make re-reading free of double counting.
The first run and `--from-start` walk from `history_start`, not from Wakapi's all-time
range, which is built from pre-computed summaries and lags behind late, old data.
After Wakapi's WakaTime.com history import, run `mnemosyne-admin import-wakapi --from-start`.
"""

import base64
import hashlib
import json
import sqlite3
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from mnemosyne.config import Config
from mnemosyne.domain import heartbeats

SOURCE = "wakapi"
RESYNC_DAYS = 7
DEEP_RESYNC_DAYS = 180
API = "/api/compat/wakatime/v1/users/current"


class WakapiError(RuntimeError):
    pass


def fingerprint(hb: dict) -> str:
    parts = [
        int(hb.get("time") or 0),
        hb.get("entity"),
        hb.get("type"),
        hb.get("category"),
        hb.get("project"),
        hb.get("branch"),
        hb.get("language"),
        bool(hb.get("is_write")),
        hb.get("machine_name_id"),
        hb.get("user_agent_id"),
    ]
    digest = hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()
    return f"wakapi:{digest[:32]}"


class WakapiClient:
    def __init__(self, base_url: str, api_key: str, transport: httpx.BaseTransport | None = None):
        if not api_key:
            raise WakapiError("MNEMOSYNE_WAKAPI_API_KEY is not set")
        token = base64.b64encode(api_key.encode()).decode()
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
            transport=transport,
        )

    def _get(self, path: str, **params) -> dict:
        response = self._http.get(API + path, params=params)
        if response.status_code == 401:
            raise WakapiError("Wakapi rejected the API key (401)")
        if response.status_code >= 400:
            raise WakapiError(f"Wakapi {path} returned {response.status_code}")
        return response.json()

    def heartbeats(self, day: date) -> list[dict]:
        return self._get("/heartbeats", date=day.isoformat()).get("data") or []


def _load_cursor(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT cursor_json FROM import_cursors WHERE source = ?", (SOURCE,)
    ).fetchone()
    return json.loads(row["cursor_json"]) if row else {}


def _save_cursor(conn: sqlite3.Connection, cursor: dict) -> None:
    conn.execute(
        "INSERT INTO import_cursors (source, cursor_json, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT (source) DO UPDATE SET cursor_json = excluded.cursor_json,"
        " updated_at = excluded.updated_at",
        (SOURCE, json.dumps(cursor), time.time()),
    )


def insert_day(conn: sqlite3.Connection, config: Config, beats: list[dict]) -> int:
    inserted = 0
    now = time.time()
    conn.execute("BEGIN IMMEDIATE")
    try:
        for hb in beats:
            cur = conn.execute(
                "INSERT OR IGNORE INTO raw_heartbeats (source_id, wakapi_id, time, machine_name,"
                " project_name, entity, entity_type, category, language, branch, is_write,"
                " user_agent, payload_json, ingested_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    fingerprint(hb),
                    hb.get("id"),
                    float(hb.get("time") or 0),
                    hb.get("machine_name_id"),
                    hb.get("project"),
                    hb.get("entity"),
                    hb.get("type"),
                    hb.get("category"),
                    hb.get("language"),
                    hb.get("branch"),
                    int(bool(hb.get("is_write"))),
                    hb.get("user_agent_id"),
                    json.dumps(hb, sort_keys=True),
                    now,
                ),
            )
            if cur.rowcount:
                inserted += 1
                row = conn.execute(
                    "SELECT * FROM raw_heartbeats WHERE id = ?", (cur.lastrowid,)
                ).fetchone()
                heartbeats.derive(conn, config, row)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return inserted


def run(
    conn: sqlite3.Connection,
    config: Config,
    client: WakapiClient | None = None,
    today: date | None = None,
    from_start: bool = False,
) -> dict:
    client = client or WakapiClient(config.wakapi.base_url, config.wakapi.api_key)
    today = today or datetime.now(ZoneInfo(config.timezone)).date()
    started = time.time()
    run_id = conn.execute(
        "INSERT INTO import_runs (source, started_at) VALUES (?, ?)", (SOURCE, started)
    ).lastrowid
    fetched = inserted = 0
    try:
        cursor = {} if from_start else _load_cursor(conn)
        deep = cursor.get("last_deep_resync") != today.isoformat()
        if from_start or "last_complete_day" not in cursor:
            day = config.wakapi.history_start
        else:
            window = DEEP_RESYNC_DAYS if deep else RESYNC_DAYS
            day = min(date.fromisoformat(cursor["last_complete_day"]),
                      today - timedelta(days=window))
        # Wakapi buckets days in the account's timezone, which may run ahead of ours
        # (e.g. UTC); always read one extra day so evening heartbeats aren't a day late.
        while day <= today + timedelta(days=1):
            beats = client.heartbeats(day)
            fetched += len(beats)
            inserted += insert_day(conn, config, beats)
            if day < today:
                cursor["last_complete_day"] = max(
                    day.isoformat(), cursor.get("last_complete_day", "")
                )
                _save_cursor(conn, cursor)
            day += timedelta(days=1)
        if deep:
            cursor["last_deep_resync"] = today.isoformat()
            _save_cursor(conn, cursor)
        conn.execute(
            "UPDATE import_runs SET finished_at = ?, fetched = ?, inserted = ? WHERE id = ?",
            (time.time(), fetched, inserted, run_id),
        )
    except Exception as exc:
        conn.execute(
            "UPDATE import_runs SET finished_at = ?, fetched = ?, inserted = ?, error = ?"
            " WHERE id = ?",
            (time.time(), fetched, inserted, str(exc), run_id),
        )
        raise
    return {"run_id": run_id, "fetched": fetched, "inserted": inserted}
