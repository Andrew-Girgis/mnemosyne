from datetime import date, datetime
from zoneinfo import ZoneInfo

from conftest import FakeWakapi, heartbeat
from fastapi.testclient import TestClient

from mnemosyne import auth
from mnemosyne.domain import heartbeats, projects
from mnemosyne.domain.audit import Actor
from mnemosyne.importers import wakapi
from mnemosyne.server.app import create_app

TZ = ZoneInfo("America/Toronto")
USER = Actor("user", "omphalos")


def ts(day: str, hh: int, mm: int = 0, ss: int = 0) -> float:
    y, m, d = map(int, day.split("-"))
    return datetime(y, m, d, hh, mm, ss, tzinfo=TZ).timestamp()


def entry(ws, relpath, path, kind="try", git=None, mtime=None):
    return projects.ScanEntry(ws, relpath, path, kind, git is not None, git, mtime)


def scan_omphalos(conn, config, *entries, complete=True):
    return projects.ingest_scan(conn, config, "omphalos", list(entries), complete, USER)


# --- migrations -------------------------------------------------------------

def test_migrations_are_idempotent(conn):
    from mnemosyne.db import migrate
    assert migrate(conn) == []


# --- projects -----------------------------------------------------------------

def test_repeated_scans_never_duplicate(conn, config):
    e = entry("personal", "tries/2026-04-14-ttc-gtfs-rt", "/home/girgie/Projects/tries/2026-04-14-ttc-gtfs-rt")
    first = scan_omphalos(conn, config, e)
    second = scan_omphalos(conn, config, e)
    assert len(first.created) == 1 and second.created == []
    assert conn.execute("SELECT count(*) FROM projects").fetchone()[0] == 1


def test_same_try_on_both_devices_is_one_project(conn, config):
    rel = "tries/2026-04-14-ttc-gtfs-rt"
    scan_omphalos(conn, config, entry("personal", rel, f"/home/girgie/Projects/{rel}"))
    projects.ingest_scan(conn, config, "macbook",
                         [entry("personal", rel, f"/Users/agirgis/Projects/{rel}")], True,
                         Actor("user", "macbook"))
    assert conn.execute("SELECT count(*) FROM projects").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM project_locations").fetchone()[0] == 2


def test_graduation_is_followed_and_old_key_still_resolves(conn, config):
    old = entry("personal", "tries/2026-09-25-mnemosyne",
                "/home/girgie/Projects/tries/2026-09-25-mnemosyne")
    scan_omphalos(conn, config, old)
    pid = projects.resolve_key(conn, "personal/tries/2026-09-25-mnemosyne")["id"]
    moved = scan_omphalos(conn, config, entry("personal", "mnemosyne",
                                              "/home/girgie/Projects/mnemosyne", kind="graduated"))
    assert moved.created == [] and moved.moved[0]["rule"] == "name"
    assert projects.resolve_key(conn, "personal/mnemosyne")["id"] == pid
    assert projects.resolve_key(conn, "personal/tries/2026-09-25-mnemosyne")["id"] == pid
    assert conn.execute("SELECT count(*) FROM audit_log WHERE op = 'project.move'").fetchone()[0] == 1


def test_move_with_same_root_commit_is_followed(conn, config):
    scan_omphalos(conn, config, entry("work", "tries/2026-03-26-statcan-to-qm",
                                      "/home/girgie/Quartermaster/tries/2026-03-26-statcan-to-qm",
                                      git="abc123"))
    moved = scan_omphalos(conn, config, entry("work", "tries/2026-03-26-statcan-qm-v2",
                                              "/home/girgie/Quartermaster/tries/2026-03-26-statcan-qm-v2",
                                              git="abc123"))
    assert moved.moved and moved.moved[0]["rule"] == "root-commit"


def test_unrelated_rename_is_suggested_not_followed(conn, config):
    scan_omphalos(conn, config, entry("personal", "tries/2026-05-12-test",
                                      "/home/girgie/Projects/tries/2026-05-12-test"))
    result = scan_omphalos(conn, config, entry("personal", "tries/2026-06-01-test",
                                               "/home/girgie/Projects/tries/2026-06-01-test"))
    assert len(result.created) == 1 and result.moved == []
    assert result.suggested and result.suggested[0]["evidence"] == "name"


def test_clones_of_same_repo_stay_separate(conn, config):
    a = entry("personal", "tries/2026-06-08-a", "/home/girgie/Projects/tries/2026-06-08-a", git="r")
    b = entry("personal", "tries/2026-06-09-b", "/home/girgie/Projects/tries/2026-06-09-b", git="r")
    scan_omphalos(conn, config, a)
    result = scan_omphalos(conn, config, a, b)  # a is still present: b is a new clone
    assert len(result.created) == 1 and result.moved == []


def test_lifecycle_seeded_by_recency(conn, config):
    import time
    scan_omphalos(conn, config,
                  entry("personal", "tries/new", "/home/girgie/Projects/tries/new", mtime=time.time()),
                  entry("personal", "tries/old", "/home/girgie/Projects/tries/old", mtime=1.0))
    got = dict(conn.execute("SELECT name, lifecycle FROM projects").fetchall())
    assert got == {"new": "active", "old": "paused"}


# --- heartbeats: origin + mapping ------------------------------------------------

def test_origin_tagging():
    assert heartbeats.origin_of("wakatime/v1 (darwin) go1.22 opencode-cli/1.15.5 opencode-wakatime/1.3.8") == "agent"
    assert heartbeats.origin_of("wakatime/v1 (darwin) go1.22 claude-code/ claude-code-wakatime/3.1.6") == "agent"
    assert heartbeats.origin_of("wakatime/v1 (linux) go1.22 pi-coding-agent/unknown") == "agent"
    assert heartbeats.origin_of("wakatime/v1 (linux) go1.22 vscode/1.99 vscode-wakatime/25") == "editor"
    # origin-v2: the 'ai coding' category wins even when the plugin label says nothing useful
    assert heartbeats.origin_of("wakatime/v2 (linux) go1.26 gpt/5.5 mnemosyne-m1-check/0.1", "ai coding") == "agent"
    assert heartbeats.origin_of("wakatime/v2 (linux) go1.26 gpt/5.4 Hermes Agent", "") == "agent"
    assert heartbeats.origin_of("wakatime/v2 (linux) go1.26 Unknown/0", "coding") == "editor"
    assert heartbeats.plugin_of("wakatime/v1 (darwin) go1.22 opencode-cli/1.15.5 opencode-wakatime/1.3.8") \
        == "opencode-cli/1.15.5 opencode-wakatime/1.3.8"


def _import(conn, config, days, today="2026-09-26", **kw):
    fake = FakeWakapi(days)
    client = wakapi.WakapiClient("http://wakapi.test", "k", transport=fake.transport())
    return wakapi.run(conn, config, client=client, today=date.fromisoformat(today), **kw), fake


def test_mac_heartbeat_maps_to_synced_try_project(conn, config):
    rel = "tries/2026-04-14-ttc-gtfs-rt"
    scan_omphalos(conn, config, entry("personal", rel, f"/home/girgie/Projects/{rel}"))
    day = "2026-09-26"
    _import(conn, config, {day: [heartbeat(ts(day, 9), f"/Users/agirgis/Projects/{rel}/main.py",
                                           machine="Andrews-Macbook-Air.local")]})
    row = conn.execute("SELECT * FROM heartbeat_derivations").fetchone()
    assert row["device_id"] == "macbook" and row["match_rule"] == "path"
    assert row["project_id"] == projects.resolve_key(conn, f"personal/{rel}")["id"]


def test_legacy_path_maps_to_split_project_or_legacy(conn, config):
    rel = "tries/2026-03-26-statcan-to-qm"
    scan_omphalos(conn, config, entry("work", rel, f"/home/girgie/Quartermaster/{rel}"))
    day = "2026-09-26"
    _import(conn, config, {day: [
        heartbeat(ts(day, 9), "/Users/agirgis/src/tries/2026-03-26-statcan-to-qm/q.sql",
                  machine="Andrews-Macbook-Air.local"),
        heartbeat(ts(day, 10), "/Users/agirgis/src/tries/latin-property-services/a.py",
                  machine="Andrews-Macbook-Air.local"),
    ]})
    rows = conn.execute(
        "SELECT p.key, d.match_rule FROM heartbeat_derivations d JOIN projects p"
        " ON p.id = d.project_id ORDER BY p.key").fetchall()
    assert [tuple(r) for r in rows] == [("legacy/latin-property-services", "legacy-path"),
                                        (f"work/{rel}", "legacy-path")]


def test_unmatched_heartbeats_are_preserved_then_matched_after_scan(conn, config):
    day = "2026-09-26"
    _import(conn, config, {day: [heartbeat(ts(day, 9), "/home/girgie/Projects/Argus/x.py",
                                           project="Argus")]})
    assert conn.execute("SELECT match_rule FROM heartbeat_derivations").fetchone()[0] == "unmatched"
    scan_omphalos(conn, config, entry("personal", "Argus", "/home/girgie/Projects/Argus",
                                      kind="graduated", git="r1"))
    heartbeats.derive_pending(conn, config, include_unmatched=True)
    assert conn.execute("SELECT match_rule FROM heartbeat_derivations").fetchone()[0] == "path"


# --- import idempotency (the main failure mode) ------------------------------------------

def test_reimport_produces_identical_counts(conn, config):
    day = "2026-09-26"
    days = {day: [heartbeat(ts(day, 9, m), f"/home/girgie/Projects/tries/x/{m}.py") for m in range(30)]}
    first, _ = _import(conn, config, days)
    second, _ = _import(conn, config, days)
    third, _ = _import(conn, config, days, from_start=True)
    assert first["inserted"] == 30 and second["inserted"] == 0 and third["inserted"] == 0
    assert conn.execute("SELECT count(*) FROM raw_heartbeats").fetchone()[0] == 30


def test_rebuilt_wakapi_with_new_row_ids_does_not_double_count(conn, config):
    day = "2026-09-26"
    beats = [heartbeat(ts(day, 9, m), "/tmp/a.py", hb_id=m) for m in range(5)]
    rebuilt = [heartbeat(ts(day, 9, m), "/tmp/a.py", hb_id=1000 + m) for m in range(5)]
    _import(conn, config, {day: beats})
    again, _ = _import(conn, config, {day: rebuilt})
    assert again["inserted"] == 0


def test_late_offline_heartbeats_within_resync_window_are_picked_up(conn, config):
    _import(conn, config, {"2026-09-20": [heartbeat(ts("2026-09-20", 9), "/tmp/a.py")]},
            today="2026-09-26")
    late = {"2026-09-22": [heartbeat(ts("2026-09-22", 14), "/tmp/b.py")]}
    result, fake = _import(conn, config, late, today="2026-09-26")
    assert result["inserted"] == 1
    assert any("date=2026-09-19" in u for u in fake.requests)  # rolling 7-day window


def test_first_run_each_day_resyncs_180_days(conn, config):
    _import(conn, config, {}, today="2026-09-26")
    _, fake = _import(conn, config, {}, today="2026-09-27")
    assert any("date=2026-03-31" in u for u in fake.requests)
    _, fake = _import(conn, config, {}, today="2026-09-27")
    assert not any("date=2026-03-31" in u for u in fake.requests)


# --- durations ---------------------------------------------------------------------

def test_overlap_across_devices_is_unioned_not_summed(conn, config):
    rel = "tries/p"
    scan_omphalos(conn, config, entry("personal", rel, f"/home/girgie/Projects/{rel}"))
    day = "2026-09-26"
    lin = [heartbeat(ts(day, 9, m), f"/home/girgie/Projects/{rel}/a.py") for m in range(0, 31, 5)]
    mac = [heartbeat(ts(day, 9, m), f"/Users/agirgis/Projects/{rel}/a.py",
                     machine="Andrews-Macbook-Air.local") for m in range(10, 41, 5)]
    _import(conn, config, {day: lin + mac})
    summary = heartbeats.coding_summary(conn, ts(day, 0), ts(day, 23, 59, 59))
    assert summary["totals"]["editor_seconds"] == 40 * 60  # 9:00–9:40, not 30 + 30 minutes


def test_agent_and_editor_time_reported_separately(conn, config):
    day = "2026-09-26"
    agent_ua = "wakatime/v1 (darwin) go1.22 claude-code/ claude-code-wakatime/3.1.6"
    _import(conn, config, {day: [
        *[heartbeat(ts(day, 9, m), "/tmp/a.py") for m in (0, 5, 10)],
        *[heartbeat(ts(day, 9, m), "/tmp/a.py", ua=agent_ua) for m in (0, 5, 10, 15)],
    ]})
    totals = heartbeats.coding_summary(conn, ts(day, 0), ts(day, 23))["totals"]
    assert totals == {"editor_seconds": 600, "agent_seconds": 900, "any_seconds": 900}


def test_gaps_beyond_timeout_are_not_counted(conn, config):
    day = "2026-09-26"
    _import(conn, config, {day: [heartbeat(ts(day, 9), "/tmp/a.py"),
                                 heartbeat(ts(day, 11), "/tmp/a.py")]})
    assert heartbeats.coding_summary(conn, ts(day, 0), ts(day, 23))["totals"]["any_seconds"] == 0


# --- API ---------------------------------------------------------------------------

def _app(config, db_path):
    return TestClient(create_app(config, db_path, background=False))


def _token(db_path, kind, device="omphalos"):
    from mnemosyne.db import connect
    c = connect(db_path)
    try:
        return auth.create_token(c, kind, device, None)[1]
    finally:
        c.close()


def test_api_requires_tokens_and_enforces_kinds(config, db_path):
    client = _app(config, db_path)
    assert client.get("/v1/projects").status_code == 401
    assert client.get("/v1/projects", headers={"Authorization": "Bearer nope"}).json()["error"]["code"] \
        == "unauthenticated"
    agent = _token(db_path, "agent")
    r = client.post("/v1/scans", json={"complete": True, "entries": []},
                    headers={"Authorization": f"Bearer {agent}"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"


def test_data_survives_restart(config, db_path):
    user = None
    with _app(config, db_path) as client:
        user = _token(db_path, "user")
        h = {"Authorization": f"Bearer {user}"}
        body = {"complete": True, "entries": [{
            "workspace": "personal", "relpath": "tries/x", "path": "/home/girgie/Projects/tries/x",
            "kind": "try", "has_git": False}]}
        assert client.post("/v1/scans", json=body, headers=h).status_code == 200
    with _app(config, db_path) as client:  # fresh app on the same database file
        h = {"Authorization": f"Bearer {user}"}
        keys = [p["key"] for p in client.get("/v1/projects", headers=h).json()["projects"]]
        assert keys == ["personal/tries/x"]
        assert client.get("/v1/projects/personal/tries/x", headers=h).json()["project"]["key"] \
            == "personal/tries/x"


def test_first_import_walks_from_history_start_through_tomorrow(conn, config):
    _, fake = _import(conn, config, {}, today="2026-09-26")
    days = [u.rsplit("date=", 1)[1] for u in fake.requests if "date=" in u]
    assert days[0] == "2026-09-15" and days[-1] == "2026-09-27"  # one day ahead for tz skew


def test_old_heartbeats_arriving_late_are_found_by_deep_resync(conn, config):
    _import(conn, config, {}, today="2026-09-26")
    late = {"2026-04-08": [heartbeat(ts("2026-04-08", 20), "Hermes Agent cron_1")]}
    result, _ = _import(conn, config, late, today="2026-09-27")  # first run of a new day
    assert result["inserted"] == 1


def test_from_start_resets_a_bad_cursor(conn, config):
    conn.execute("INSERT INTO import_cursors VALUES ('wakapi', '{\"last_complete_day\": \"2000-01-01\"}', 0)")
    _import(conn, config, {}, today="2026-09-26", from_start=True)
    _, fake = _import(conn, config, {}, today="2026-09-26")
    assert not any("date=2000-" in u for u in fake.requests)


# --- graduation symlinks, merge, stamped heartbeats ------------------------------------

GRAD = entry("personal", "mnemosyne", "/home/girgie/Projects/mnemosyne", kind="graduated", git="r0")
LINK = {"workspace": "personal", "relpath": "tries/2026-09-25-mnemosyne",
        "target_path": "/home/girgie/Projects/mnemosyne"}


def test_try_symlink_becomes_alias_of_graduated_project(conn, config):
    result = projects.ingest_scan(conn, config, "omphalos", [GRAD], True, USER, links=[LINK])
    assert result.links == [{"alias": "personal/tries/2026-09-25-mnemosyne",
                             "project_id": projects.resolve_key(conn, "personal/mnemosyne")["id"]}]
    assert projects.resolve_key(conn, "personal/tries/2026-09-25-mnemosyne")["key"] == "personal/mnemosyne"


def test_duplicate_made_from_symlink_is_merged_in(conn, config):
    dup = entry("personal", "tries/2026-09-25-mnemosyne",
                "/home/girgie/Projects/tries/2026-09-25-mnemosyne")
    scan_omphalos(conn, config, dup, GRAD)  # what the pre-fix scanner reported
    result = projects.ingest_scan(conn, config, "omphalos", [GRAD], True, USER, links=[LINK])
    assert result.links[0]["merged"] == "personal/tries/2026-09-25-mnemosyne"
    assert conn.execute("SELECT count(*) FROM projects").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM audit_log WHERE op = 'project.merge'").fetchone()[0] == 1


def test_stamped_heartbeats_do_not_match_by_project_name(conn, config):
    scan_omphalos(conn, config, GRAD)
    day = "2026-09-26"
    stamped_ua = "wakatime/v2 (linux) go1.26 gpt/5.4 Hermes Agent mnemosyne-m1-check/0.1"
    _import(conn, config, {day: [
        heartbeat(ts(day, 9), "Hermes Agent cron_1", project="mnemosyne", ua=stamped_ua),
        heartbeat(ts(day, 9, 5), "Claude abc", project="mnemosyne",
                  ua="wakatime/v2 (linux) go1.26 opus/5-5 claude-code/2.1"),
        heartbeat(ts(day, 9, 10), "OpenCode ses_1", project="Argus", ua=stamped_ua),
    ]})
    scan_omphalos(conn, config, GRAD, entry("personal", "Argus", "/home/girgie/Projects/Argus",
                                            kind="graduated", git="r1"))
    heartbeats.derive_pending(conn, config, include_unmatched=True)
    rules = [r[0] for r in conn.execute(
        "SELECT d.match_rule FROM heartbeat_derivations d JOIN raw_heartbeats h"
        " ON h.id = d.heartbeat_id ORDER BY h.time")]
    assert rules == ["unmatched-stamped", "name", "name"]  # a genuine name survives the label


def test_merge_api_is_user_only_and_keeps_old_key(config, db_path):
    client = _app(config, db_path)
    user, agent = _token(db_path, "user"), _token(db_path, "agent")
    h = {"Authorization": f"Bearer {user}"}
    for rel, kind in (("tries/2026-01-01-a", "try"), ("a", "graduated")):
        client.post("/v1/scans", headers=h, json={"complete": False, "entries": [{
            "workspace": "personal", "relpath": rel, "path": f"/home/girgie/Projects/{rel}",
            "kind": kind, "has_git": False}]})
    body = {"into": "personal/a"}
    denied = client.post("/v1/projects/personal/tries/2026-01-01-a/merge", json=body,
                         headers={"Authorization": f"Bearer {agent}"})
    assert denied.status_code == 403
    ok = client.post("/v1/projects/personal/tries/2026-01-01-a/merge", json=body, headers=h)
    assert ok.status_code == 200 and ok.json()["into"]["key"] == "personal/a"
    assert client.get("/v1/projects/personal/tries/2026-01-01-a", headers=h).json()["project"]["key"] == "personal/a"


def test_partial_scan_never_infers_a_move(conn, config):
    scan_omphalos(conn, config, entry("personal", "tries/2026-01-01-a", "/home/girgie/Projects/tries/2026-01-01-a"))
    result = scan_omphalos(conn, config, entry("personal", "a", "/home/girgie/Projects/a", kind="graduated"),
                           complete=False)
    assert result.moved == [] and len(result.created) == 1


def test_scanner_includes_non_git_folders_and_skips_hidden_and_symlinks(tmp_path):
    from mnemosyne import scanner
    base = tmp_path / "Projects"
    tries = base / "tries"
    for d in (tries / "2026-01-01-a", base / "meta-audience-sync", base / ".hidden", base / "repo" / ".git"):
        d.mkdir(parents=True)
    (tries / "2026-01-02-grad").symlink_to(base / "repo")
    entries, links, repos, complete = scanner.scan(
        [{"name": "personal", "base": str(base), "try_root": str(tries)}])
    got = sorted((e["relpath"], e["kind"], e["has_git"]) for e in entries)
    assert got == [("meta-audience-sync", "graduated", False), ("repo", "graduated", True),
                   ("tries/2026-01-01-a", "try", False)]
    assert links == [{"workspace": "personal", "relpath": "tries/2026-01-02-grad",
                      "target_path": str(base / "repo")}] and complete
    assert [r["name"] for r in repos] == ["repo"]  # an empty .git dir: repo reported, no commits


def test_mac_paths_match_case_insensitively(conn, config):
    rel = "tries/2026-05-12-qm-marketing-test"
    scan_omphalos(conn, config, entry("work", rel, f"/home/girgie/Quartermaster/{rel}"))
    key = projects.key_for_path(config, "macbook", f"/Users/agirgis/QuarterMaster/{rel}/a.sql")
    assert projects.resolve_key(conn, key)["key"] == f"work/{rel}"
    assert projects.key_for_path(config, "omphalos", f"/home/girgie/QuarterMaster/{rel}/a") is None
