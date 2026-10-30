import pytest
from conftest import heartbeat
from fastapi.testclient import TestClient

from mnemosyne import auth, client
from mnemosyne.db import connect
from mnemosyne.domain import commits, projects, selection, tasks
from mnemosyne.domain.audit import Actor
from mnemosyne.domain.errors import DomainError
from mnemosyne.server.app import create_app

USER = Actor("user", "omphalos")
AGENT = Actor("agent", "omphalos", "claude-code", "sess-1")
TRY = "tries/2026-04-14-ttc-gtfs-rt"


def seed(conn, config):
    projects.ingest_scan(conn, config, "omphalos", [projects.ScanEntry(
        "personal", TRY, f"/home/girgie/Projects/{TRY}", "try", True, "root1", None)], True, USER)
    return projects.resolve_key(conn, f"personal/{TRY}")


# --- tasks ------------------------------------------------------------------------------


def test_task_lifecycle_is_audited(conn, config):
    p = seed(conn, config)
    t = tasks.create(conn, config, USER, project=p["key"], title="Parse feed")
    assert t["column_name"] == "Inbox"
    tasks.move(conn, config, AGENT, t["id"], "active")  # case-insensitive column
    tasks.update(conn, AGENT, t["id"], title="Parse GTFS-RT feed")
    tasks.add_note(conn, AGENT, t["id"], "protobuf schema pinned")
    detail = tasks.detail(conn, t["id"])
    assert detail["task"]["column_name"] == "Active"
    assert [h["op"] for h in detail["history"]] == ["task.create", "task.move", "task.update",
                                                    "task.note"]
    assert detail["notes"][0]["actor_harness"] == "claude-code"


def test_agents_cannot_archive_and_bad_columns_are_rejected(conn, config):
    p = seed(conn, config)
    t = tasks.create(conn, config, AGENT, project=p["id"], title="x")
    with pytest.raises(DomainError) as err:
        tasks.set_archived(conn, AGENT, t["id"], True)
    assert err.value.status == 403
    with pytest.raises(DomainError) as err:
        tasks.move(conn, config, USER, t["id"], "Someday")
    assert err.value.code == "invalid_column"
    tasks.set_archived(conn, USER, t["id"], True)
    assert tasks.list_tasks(conn, project=p["id"]) == []
    assert len(tasks.list_tasks(conn, project=p["id"], include_archived=True)) == 1


# --- selection ----------------------------------------------------------------------------


def test_agent_select_links_session_and_never_touches_device_selection(conn, config):
    p = seed(conn, config)
    mine = tasks.create(conn, config, USER, project=p["id"], title="mine")
    theirs = tasks.create(conn, config, USER, project=p["id"], title="theirs")
    selection.select(conn, USER, "omphalos", task=mine["id"])
    with pytest.raises(DomainError):
        selection.select(conn, AGENT, "omphalos", task=theirs["id"])
    with pytest.raises(DomainError):
        selection.clear(conn, AGENT, "omphalos")
    selection.link_session(conn, AGENT, harness="claude-code", session_id="sess-1",
                           task=theirs["id"])
    with pytest.raises(DomainError):  # not its own session
        selection.link_session(conn, AGENT, harness="codex", session_id="other", task=None)
    assert selection.current(conn, "omphalos")[0]["task_id"] == mine["id"]
    assert tasks.detail(conn, theirs["id"])["sessions"][0]["session_id"] == "sess-1"


def test_collector_can_expire_selection(conn, config):
    p = seed(conn, config)
    selection.select(conn, USER, "omphalos", project=p["id"])
    selection.clear(conn, Actor("system", "omphalos", "collector"), "omphalos", "lock")
    now = selection.current(conn, "omphalos")[0]
    assert now["project_id"] is None and now["reason"] == "lock"


# --- projects -----------------------------------------------------------------------------


def test_lifecycle_is_user_only_but_agents_may_create_projects(conn, config):
    p = seed(conn, config)
    with pytest.raises(DomainError):
        projects.set_lifecycle(conn, AGENT, p["id"], "paused")
    assert projects.set_lifecycle(conn, USER, p["id"], "paused")["lifecycle"] == "paused"
    made = projects.create_manual(conn, config, AGENT, name="Grant writing", workspace="work")
    assert made["key"] == "work/grant-writing" and made["kind"] == "manual"
    with pytest.raises(DomainError):
        projects.create_manual(conn, config, AGENT, name="grant writing", workspace="work")


def test_accepting_a_move_keeps_history_under_the_new_key(conn, config):
    old = projects.ingest_scan(conn, config, "omphalos", [projects.ScanEntry(
        "personal", "tries/2026-05-12-test", "/home/girgie/Projects/tries/2026-05-12-test",
        "try", False, None, None)], True, USER)
    old_id = old.created[0]
    t = tasks.create(conn, config, USER, project=old_id, title="keep me")
    moved = projects.ingest_scan(conn, config, "omphalos", [projects.ScanEntry(
        "personal", "tries/2026-06-01-test", "/home/girgie/Projects/tries/2026-06-01-test",
        "try", False, None, None)], True, USER)
    suggestion = projects.open_suggestions(conn)[0]
    projects.resolve_suggestion(conn, USER, suggestion["id"], accept=True)
    survivor = projects.resolve_key(conn, "personal/tries/2026-06-01-test")
    assert survivor["id"] == old_id and moved.created[0] != old_id
    assert projects.resolve_key(conn, "personal/tries/2026-05-12-test")["id"] == old_id
    assert tasks.get(conn, t["id"])["project_id"] == old_id
    assert projects.open_suggestions(conn) == []


# --- commits ------------------------------------------------------------------------------


def test_commits_from_working_copy_and_mirror_map_to_one_project(conn, config):
    p = seed(conn, config)
    c1 = {"sha": "a" * 40, "authored_at": 1.0, "committed_at": 2.0, "subject": "init"}
    c2 = {"sha": "b" * 40, "authored_at": 3.0, "committed_at": 4.0, "subject": "feed"}
    first = commits.ingest(conn, config, "omphalos", [
        {"path": f"/home/girgie/Projects/{TRY}", "name": TRY.split("/")[1], "commits": [c1]},
        {"path": "/home/girgie/.local/share/try-git/personal/2026-04-14-ttc-gtfs-rt.git",
         "name": "2026-04-14-ttc-gtfs-rt", "mirror_workspace": "personal", "commits": [c1, c2]},
    ])
    again = commits.ingest(conn, config, "omphalos", [
        {"path": f"/home/girgie/Projects/{TRY}", "name": "x", "commits": [c1, c2]}])
    assert first["new_commits"] == 2 and again["new_commits"] == 0
    assert [c["subject"] for c in commits.for_project(conn, p["id"])] == ["feed", "init"]


# --- API through the real client ------------------------------------------------------------


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
    agent = client.Client(client.Identity("agent", "codex", "thread-9"), agent_tok, "http://t",
                          transport)
    return user, agent


def test_agent_workflow_over_http_is_non_interactive_json(config, db_path):
    user, agent = _clients(config, db_path)
    user.post("/v1/scans", {"complete": True, "entries": [{
        "workspace": "personal", "relpath": TRY, "path": f"/home/girgie/Projects/{TRY}",
        "kind": "try", "has_git": False}]})
    created = agent.post("/v1/tasks", {"project": f"personal/{TRY}", "title": "Write parser"})
    task_id = created["task"]["id"]
    agent.post(f"/v1/tasks/{task_id}/move", {"column": "Active"})
    agent.post(f"/v1/tasks/{task_id}/notes", {"body": "done: vehicle positions"})
    agent.put("/v1/sessions/codex/thread-9/task", {"task": task_id})
    with pytest.raises(client.ClientError) as err:
        agent.put("/v1/selection", {"task": task_id})
    assert err.value.exit_code == 3 and err.value.code == "forbidden"
    with pytest.raises(client.ClientError) as err:
        agent.post(f"/v1/tasks/{task_id}/archive")
    assert err.value.exit_code == 3
    with pytest.raises(client.ClientError) as err:
        agent.get("/v1/tasks/tsk_nope")
    assert err.value.exit_code == 4
    board = user.get(f"/v1/projects/personal/{TRY}/board")
    assert [t["title"] for t in board["columns"]["Active"]] == ["Write parser"]
    detail = user.get(f"/v1/tasks/{task_id}")
    assert {h["actor_harness"] for h in detail["history"]} == {"codex"}
    assert detail["sessions"][0]["session_id"] == "thread-9"
    assert user.get("/v1/selection")["selections"] == []


def test_board_reports_coding_split_by_origin(config, db_path, monkeypatch):
    from datetime import date as real_date

    user, _ = _clients(config, db_path)
    user.post("/v1/scans", {"complete": True, "entries": [{
        "workspace": "personal", "relpath": TRY, "path": f"/home/girgie/Projects/{TRY}",
        "kind": "try", "has_git": False}]})
    import conftest

    from mnemosyne.importers import wakapi
    c = connect(db_path)
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("America/Toronto")).replace(hour=0, minute=0, second=0)
        base = today.timestamp() + 3600
        beats = [heartbeat(base + 60 * m, f"/home/girgie/Projects/{TRY}/a.py") for m in (0, 5, 10)]
        fake = conftest.FakeWakapi({today.date().isoformat(): beats})
        wakapi.run(c, config, client=wakapi.WakapiClient("http://w", "k", fake.transport()),
                   today=today.date())
    finally:
        c.close()
    board = user.get(f"/v1/projects/personal/{TRY}/board")
    assert board["coding"]["today"]["editor_seconds"] == 600
    assert board["coding"]["today"]["agent_seconds"] == 0
    assert real_date  # silence unused import in older pythons


# --- caller detection ---------------------------------------------------------------------


@pytest.mark.parametrize(("env", "expected"), [
    ({}, ("user", None, None)),
    ({"AI_AGENT": "claude-code_2-1-283_agent", "CLAUDE_CODE_SESSION_ID": "abc"},
     ("agent", "claude-code", "abc")),
    ({"AI_AGENT": "pi"}, ("agent", "pi", None)),
    ({"AI_AGENT": "hermes-agent"}, ("agent", "hermes", None)),
    ({"PI_CODING_AGENT": "true"}, ("agent", "pi", None)),
    ({"CLAUDECODE": "1"}, ("agent", "claude-code", None)),
    ({"OPENCODE_EXPERIMENTAL": "1"}, ("user", None, None)),  # set in normal shells here
    ({"AI_AGENT": "pi", "MNEMOSYNE_ACTOR": "user"}, ("user", None, None)),
    ({"MNEMOSYNE_ACTOR_HARNESS": "codex", "MNEMOSYNE_ACTOR_SESSION": "t1"},
     ("agent", "codex", "t1")),
])
def test_detect(env, expected):
    who = client.detect(env=env)
    assert (who.kind, who.harness, who.session) == expected


def test_explicit_actor_flag_wins():
    assert client.detect("user", env={"AI_AGENT": "pi"}).kind == "user"
    assert client.detect("agent", env={}).kind == "agent"
