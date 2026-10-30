"""Milestone 3: ideas, project descriptions, start dates, task timelines, Try starts."""

import os
import shutil
import stat
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from conftest import CONFIG, heartbeat
from fastapi.testclient import TestClient

from mnemosyne import auth, client, trystart
from mnemosyne import config as config_mod
from mnemosyne.db import connect
from mnemosyne.domain import heartbeats, ideas, projects, tasks
from mnemosyne.domain.audit import Actor
from mnemosyne.domain.errors import DomainError
from mnemosyne.server.app import create_app

USER = Actor("user", "omphalos")
AGENT = Actor("agent", "omphalos", "claude-code", "sess-1")
TZ = ZoneInfo("America/Toronto")


def _scan(conn, config, relpath, *, workspace="personal", kind="try", mtime=None, git=None):
    base = config.devices["omphalos"].expand(config.workspaces[workspace].base)
    projects.ingest_scan(conn, config, "omphalos", [projects.ScanEntry(
        workspace, relpath, f"{base}/{relpath}", kind, bool(git), git, mtime)], False, USER)
    return projects.resolve_key(conn, f"{workspace}/{relpath}")


# --- ideas ------------------------------------------------------------------------------


def test_idea_lifecycle_is_audited_and_start_seeds_the_description(conn, config):
    idea = ideas.create(conn, config, AGENT, title="  Garden sensor ", workspace="personal",
                        summary="Soil moisture and weather.", body="ESP32 + LoRa.")
    assert idea["title"] == "Garden sensor" and idea["status"] == "open"
    ideas.update(conn, config, USER, idea["id"], summary="Soil moisture.", workspace="work")
    assert ideas.update(conn, config, USER, idea["id"], title="Garden sensor")["workspace"] == \
        "work"  # unchanged fields are not re-audited
    p = _scan(conn, config, "tries/2026-09-26-garden-sensor", workspace="work")
    started = ideas.start(conn, USER, idea["id"], p["key"])
    assert started["status"] == "started" and started["project_id"] == p["id"]
    assert projects.get(conn, p["id"])["description"] == "Soil moisture.\n\nESP32 + LoRa."
    assert ideas.list_ideas(conn) == []  # started ideas leave the open list
    assert ideas.list_ideas(conn, status="started")[0]["project_key"] == p["key"]
    assert ideas.for_project(conn, p["id"])["title"] == "Garden sensor"
    ops = [h["op"] for h in ideas.detail(conn, idea["id"])["history"]]
    assert ops == ["idea.create", "idea.update", "idea.start"]
    with pytest.raises(DomainError) as err:
        ideas.start(conn, USER, idea["id"], p["key"])
    assert err.value.code == "not_open"


def test_start_keeps_an_existing_description(conn, config):
    p = _scan(conn, config, "tries/2026-09-26-x")
    projects.set_description(conn, USER, p["id"], "Mine.")
    idea = ideas.create(conn, config, USER, title="x", workspace="personal", summary="Theirs.")
    ideas.start(conn, USER, idea["id"], p["id"])
    assert projects.get(conn, p["id"])["description"] == "Mine."


def test_dropping_is_user_only_reversible_and_validated(conn, config):
    idea = ideas.create(conn, config, AGENT, title="Home media server", workspace="personal")
    with pytest.raises(DomainError) as err:
        ideas.set_dropped(conn, AGENT, idea["id"], True)
    assert err.value.status == 403
    assert ideas.set_dropped(conn, USER, idea["id"], True)["status"] == "dropped"
    assert ideas.list_ideas(conn) == []
    assert ideas.set_dropped(conn, USER, idea["id"], False)["status"] == "open"
    for bad in ({"title": " "}, {"workspace": "hobby"}):
        with pytest.raises(DomainError):
            ideas.update(conn, config, USER, idea["id"], **bad)
    with pytest.raises(DomainError):
        ideas.create(conn, config, USER, title="t", workspace="hobby")
    with pytest.raises(DomainError):
        ideas.list_ideas(conn, status="someday")


def test_merging_projects_carries_the_idea_link(conn, config):
    a = _scan(conn, config, "tries/2026-09-26-a")
    b = _scan(conn, config, "b", kind="graduated")
    idea = ideas.create(conn, config, USER, title="a", workspace="personal")
    ideas.start(conn, USER, idea["id"], a["id"])
    projects.merge(conn, USER, a, b)
    assert ideas.get(conn, idea["id"])["project_id"] == b["id"]


# --- start dates, timelines, stamped entities -----------------------------------------------


def test_start_date_prefers_the_try_date_then_earliest_evidence(conn, config):
    dated = _scan(conn, config, "tries/2026-09-25-mnemosyne")
    graduated = _scan(conn, config, "mnemosyne", kind="graduated")
    conn.execute("INSERT INTO project_aliases VALUES (?, ?, 'moved:name', 0)",
                 ("personal/tries/2026-09-25-mnemosyne", graduated["id"]))
    plain = _scan(conn, config, "Argus", kind="graduated")
    conn.execute("INSERT INTO raw_commits (sha, authored_at, committed_at, observed_at)"
                 " VALUES ('c1', 100, 1000.0, 0)")
    conn.execute("INSERT INTO commit_projects VALUES ('c1', ?, 'v')", (plain["id"],))
    got = projects.start_dates(conn, TZ)
    sept25 = datetime(2026, 9, 25, tzinfo=TZ).timestamp()
    assert got[dated["id"]] == {"started_at": sept25, "started_source": "try folder"}
    assert got[graduated["id"]] == {"started_at": sept25, "started_source": "try folder"}
    assert got[plain["id"]] == {"started_at": 1000.0, "started_source": "first commit"}


def test_task_timeline_comes_from_the_audit_log(conn, config):
    p = _scan(conn, config, "tries/2026-09-26-t")
    t = tasks.create(conn, config, USER, project=p["id"], title="t")
    assert tasks.timelines(conn, [t["id"]])[t["id"]]["started_at"] is None
    for column in ("Active", "Paused", "Active", "Done"):
        tasks.move(conn, config, USER, t["id"], column)
    line = tasks.timelines(conn, [t["id"]])[t["id"]]
    assert [h["column"] for h in line["column_history"]] == [
        "Inbox", "Active", "Paused", "Active", "Done"]
    assert line["started_at"] == line["column_history"][1]["at"]
    assert line["done_at"] == line["column_history"][-1]["at"] and line["paused_at"] is None
    assert tasks.detail(conn, t["id"])["task"]["done_at"] == line["done_at"]


def test_stamped_directory_entity_no_longer_matches_by_path(conn, config):
    from test_foundation import _import, ts

    _scan(conn, config, "mnemosyne", kind="graduated")
    stamped = "wakatime/v2 (linux) go1.26 gpt/5.4 Hermes Agent mnemosyne-m1-check/0.1"
    day = "2026-09-26"
    _import(conn, config, {day: [
        heartbeat(ts(day, 9), "/home/girgie/Projects/mnemosyne", project="mnemosyne",
                  ua=stamped),
        heartbeat(ts(day, 9, 5), "/home/girgie/Projects/mnemosyne/src/a.py", ua=stamped),
    ]})
    rules = [r[0] for r in conn.execute(
        "SELECT d.match_rule FROM heartbeat_derivations d JOIN raw_heartbeats h"
        " ON h.id = d.heartbeat_id ORDER BY h.time")]
    assert rules == ["unmatched-stamped", "path"]  # a real file inside still matches


def test_outdated_derivations_are_rederived_once(conn, config):
    from test_foundation import _import, ts

    _scan(conn, config, "mnemosyne", kind="graduated")
    stamped = "wakatime/v2 (linux) go1.26 Hermes Agent mnemosyne-m1-check/0.1"
    _import(conn, config, {"2026-09-26": [
        heartbeat(ts("2026-09-26", 9), "/home/girgie/Projects/mnemosyne", ua=stamped)]})
    conn.execute("UPDATE heartbeat_derivations SET rule_version = 'origin-v2+project-map-v2',"
                 " match_rule = 'path'")
    assert heartbeats.derive_outdated(conn, config) == 1
    assert heartbeats.derive_outdated(conn, config) == 0
    assert conn.execute("SELECT match_rule FROM heartbeat_derivations").fetchone()[0] == \
        "unmatched-stamped"


# --- API --------------------------------------------------------------------------------------


@pytest.fixture
def home_config(tmp_path, monkeypatch):
    """Config whose omphalos home is a temp dir, so Try folders can really be created."""
    monkeypatch.setenv("MNEMOSYNE_WAKAPI_API_KEY", "test-key")
    home = tmp_path / "home"
    (home / "Projects" / "tries").mkdir(parents=True)
    path = tmp_path / "home.toml"
    path.write_text(CONFIG.replace('home = "/home/girgie"', f'home = "{home}"'))
    return config_mod.load(path), home


def _clients(config, db_path):
    app = create_app(config, db_path, background=False)
    transport = TestClient(app)._transport
    c = connect(db_path)
    try:
        user_tok = auth.create_token(c, "user", "omphalos", None)[1]
        agent_tok = auth.create_token(c, "agent", "omphalos", None)[1]
    finally:
        c.close()
    user = client.Client(client.Identity("user"), user_tok, "http://t", transport)
    agent = client.Client(client.Identity("agent", "codex", "t9"), agent_tok, "http://t",
                          transport)
    return user, agent


def test_ideas_and_descriptions_over_http(config, db_path):
    user, agent = _clients(config, db_path)
    made = agent.post("/v1/ideas", {"title": "Reading tracker", "workspace": "personal",
                                    "summary": "Books and highlights."})["idea"]
    agent.patch(f"/v1/ideas/{made['id']}", {"body": "Kobo export first."})
    with pytest.raises(client.ClientError) as err:
        agent.post(f"/v1/ideas/{made['id']}/drop")
    assert err.value.exit_code == 3
    assert [i["title"] for i in user.get("/v1/ideas")["ideas"]] == ["Reading tracker"]
    user.post("/v1/scans", {"complete": False, "entries": [{
        "workspace": "personal", "relpath": "tries/2026-09-26-reading-tracker",
        "path": "/home/girgie/Projects/tries/2026-09-26-reading-tracker", "kind": "try",
        "has_git": False, "newest_mtime": 9e9}]})
    key = "personal/tries/2026-09-26-reading-tracker"
    out = user.post(f"/v1/ideas/{made['id']}/start", {"project": key})
    assert out["project"]["description"] == "Books and highlights.\n\nKobo export first."
    assert user.get("/v1/ideas", status="all")["ideas"][0]["status"] == "started"
    board = user.get(f"/v1/projects/{key}/board")
    assert board["idea"]["title"] == "Reading tracker"
    assert board["project"]["started_source"] == "try folder"
    agent.patch(f"/v1/projects/{key}", {"description": "Rewritten by an agent."})
    listed = user.get("/v1/projects")["projects"][0]
    assert listed["description"] == "Rewritten by an agent."
    assert {"started_at", "any_seconds", "editor_seconds", "last_activity_at"} <= set(listed)
    with pytest.raises(client.ClientError):
        user.patch(f"/v1/projects/{key}", {})


def test_board_tasks_carry_their_dates(config, db_path):
    user, _ = _clients(config, db_path)
    user.post("/v1/scans", {"complete": False, "entries": [{
        "workspace": "personal", "relpath": "tries/2026-09-26-b",
        "path": "/home/girgie/Projects/tries/2026-09-26-b", "kind": "try", "has_git": False}]})
    t = user.post("/v1/tasks", {"project": "personal/tries/2026-09-26-b", "title": "t"})["task"]
    user.post(f"/v1/tasks/{t['id']}/move", {"column": "Active"})
    active = user.get("/v1/projects/personal/tries/2026-09-26-b/board")["columns"]["Active"]
    assert active[0]["started_at"] and active[0]["done_at"] is None


# --- Try starts -----------------------------------------------------------------------------


def test_slugify_matches_try_naming():
    assert trystart.slugify("Garden sensor (v2)!") == "garden-sensor-v2"
    assert trystart.slugify("  Local AI dashboard  ") == "local-ai-dashboard"
    assert trystart.slugify("***") == ""


def _fake_try(tmp_path, script: str) -> str:
    exe = tmp_path / "bin" / "try"
    exe.parent.mkdir(exist_ok=True)
    exe.write_text("#!/bin/sh\n" + script)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return str(exe)


def test_try_start_creates_registers_and_links(home_config, db_path, tmp_path):
    config, home = home_config
    user, _ = _clients(config, db_path)
    fake = _fake_try(tmp_path, 'test "$1 $2" = "exec ." || exit 9\n'
                     'echo "# if you can read this"\n'
                     'echo "mkdir -p \'$TRY_PATH/2026-09-26-$3\' && \\\\"\n'
                     'echo "  cd \'$TRY_PATH/2026-09-26-$3\'"\n')
    idea = user.post("/v1/ideas", {"title": "Garden sensor", "workspace": "personal",
                                   "summary": "Soil."})["idea"]
    out = trystart.start_idea(user, idea["id"], "personal", "garden-sensor", try_bin=fake)
    folder = home / "Projects" / "tries" / "2026-09-26-garden-sensor"
    assert out["path"] == str(folder) and folder.is_dir()
    assert out["project"]["key"] == "personal/tries/2026-09-26-garden-sensor"
    assert out["project"]["lifecycle"] == "active" and out["project"]["description"] == "Soil."
    with pytest.raises(client.ClientError) as err:  # already started
        trystart.start_idea(user, idea["id"], "personal", "garden-sensor", try_bin=fake)
    assert err.value.code == "not_open"


def test_try_start_refuses_paths_outside_the_try_root(home_config, db_path, tmp_path):
    config, _home = home_config
    user, _ = _clients(config, db_path)
    fake = _fake_try(tmp_path, "echo \"mkdir -p '/tmp/elsewhere/2026-09-26-$3'\"\n")
    idea = user.post("/v1/ideas", {"title": "x", "workspace": "personal"})["idea"]
    with pytest.raises(client.ClientError) as err:
        trystart.start_idea(user, idea["id"], "personal", "x", try_bin=fake)
    assert err.value.code == "try_unexpected"
    assert user.get(f"/v1/ideas/{idea['id']}")["idea"]["status"] == "open"
    with pytest.raises(client.ClientError) as err:
        trystart.start_idea(user, idea["id"], "personal", "Bad Name", try_bin=fake)
    assert err.value.code == "invalid_name"
    with pytest.raises(client.ClientError) as err:
        trystart.start_idea(user, idea["id"], "personal", "x", try_bin="no-such-try-cli")
    assert err.value.code == "try_missing"


@pytest.mark.skipif(shutil.which("try") is None, reason="the real try CLI is not installed")
def test_real_try_cli_plans_a_dated_unique_folder(tmp_path):
    root = tmp_path / "tries"
    root.mkdir()
    today = datetime.now().strftime("%Y-%m-%d")
    first = trystart.planned_path(str(root), str(tmp_path), "garden-sensor")
    assert first == root / f"{today}-garden-sensor" and not first.exists()
    first.mkdir()
    second = trystart.planned_path(str(root), str(tmp_path), "garden-sensor")
    assert second == root / f"{today}-garden-sensor-2"
    assert os.listdir(root) == [first.name]  # planning never creates anything itself


def test_try_start_reports_a_folder_that_cannot_be_created(home_config, db_path, tmp_path):
    config, home = home_config
    user, _ = _clients(config, db_path)
    fake = _fake_try(tmp_path, 'echo "mkdir -p \'$TRY_PATH/2026-09-26-$3\'"\n')
    (home / "Projects" / "tries" / "2026-09-26-taken").mkdir()  # try said it was free
    idea = user.post("/v1/ideas", {"title": "taken", "workspace": "personal"})["idea"]
    with pytest.raises(client.ClientError) as err:
        trystart.start_idea(user, idea["id"], "personal", "taken", try_bin=fake)
    assert err.value.code == "try_folder"
    assert user.get(f"/v1/ideas/{idea['id']}")["idea"]["status"] == "open"
