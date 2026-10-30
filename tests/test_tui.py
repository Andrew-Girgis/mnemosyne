"""The TUI driven headless through the real API: Home, ideas → try, the board, themes."""

import asyncio
import stat

import pytest
from conftest import CONFIG
from test_m3 import _clients
from textual.worker import WorkerCancelled

from mnemosyne import config as config_mod
from mnemosyne.tui import theming
from mnemosyne.tui.app import MnemosyneApp
from mnemosyne.tui.modals import DetailsOverlay, HelpScreen, IdeaActions, StartProject
from mnemosyne.tui.screens import BoardScreen, HomeScreen
from mnemosyne.tui.widgets import AddItem, Divider, IdeaCard, ProjectCard, TaskCard

TRY = "tries/2026-04-14-ttc-gtfs-rt"


@pytest.fixture
def env(tmp_path, monkeypatch, db_path):
    """A server whose omphalos home is a temp dir, a fake `try` on PATH, and TUI prefs
    and theme dirs in tmp."""
    monkeypatch.setenv("MNEMOSYNE_WAKAPI_API_KEY", "test-key")
    home = tmp_path / "home"
    (home / "Projects" / "tries").mkdir(parents=True)
    cfg_path = tmp_path / "home.toml"
    cfg_path.write_text(CONFIG.replace('home = "/home/girgie"', f'home = "{home}"'))
    config = config_mod.load(cfg_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "try"
    fake.write_text('#!/bin/sh\necho "mkdir -p \'$TRY_PATH/2026-09-26-$3\' && \\\\"\n')
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    user, _ = _clients(config, db_path)
    user.post("/v1/scans", {"complete": False, "entries": [{
        "workspace": "personal", "relpath": TRY, "path": f"{home}/Projects/{TRY}",
        "kind": "try", "has_git": False, "newest_mtime": 9e9}]})
    return {"user": user, "home": home, "prefs": tmp_path / "tui.toml",
            "themes": tmp_path / "themes"}


def make_app(env) -> MnemosyneApp:
    return MnemosyneApp(env["user"], prefs_path=env["prefs"], themes_dir=env["themes"])


async def settle(pilot, app, rounds: int = 3) -> None:
    """Workers chain (mutate → reload → load); let every round finish."""
    for _ in range(rounds):
        await pilot.pause(0.1)
        try:
            await app.workers.wait_for_complete()
        except WorkerCancelled:  # an exclusive worker was superseded by a newer one
            pass
    await pilot.pause(0.1)


def run(coro):
    asyncio.run(coro)


def items(app, list_id: str) -> list:
    return list(app.screen.query_one(f"#{list_id}").children)


# --- Home --------------------------------------------------------------------------------


def test_home_shows_ideas_projects_and_adds_an_idea(env):
    user = env["user"]
    user.post("/v1/ideas", {"title": "Garden sensor", "workspace": "work",
                            "summary": "Soil moisture."})

    async def drive():
        app = make_app(env)
        async with app.run_test(size=(160, 44)) as pilot:
            await settle(pilot, app)
            assert isinstance(app.screen, HomeScreen)
            ideas = items(app, "ideas")
            assert isinstance(ideas[0], AddItem) and isinstance(ideas[1], IdeaCard)
            assert "ws-work" in ideas[1].classes
            projects = items(app, "projects")
            assert isinstance(projects[0], ProjectCard) and "ws-personal" in projects[0].classes
            assert "try folder" in app.screen.details_markup  # project details shown
            await pilot.press("i")
            await pilot.pause(0.2)
            await pilot.press(*"Reading tracker")
            await pilot.press("enter", *"Books and highlights.")
            await pilot.press("ctrl+s")
            await settle(pilot, app)
            titles = [c.idea["title"] for c in items(app, "ideas") if isinstance(c, IdeaCard)]
            assert titles == ["Reading tracker", "Garden sensor"]  # newest first
            assert app.screen.focused.id == "ideas"
            assert "Reading tracker" in app.screen.details_markup

    run(drive())
    made = env["user"].get("/v1/ideas")["ideas"][0]
    assert (made["title"], made["summary"], made["workspace"]) == \
        ("Reading tracker", "Books and highlights.", "personal")


def test_open_idea_then_edit_and_start_a_project_with_try(env):
    user = env["user"]
    idea = user.post("/v1/ideas", {"title": "Garden Sensor!", "workspace": "personal",
                                   "summary": "Soil."})["idea"]

    async def drive():
        app = make_app(env)
        async with app.run_test(size=(160, 44)) as pilot:
            await settle(pilot, app)
            await pilot.press("1", "down", "enter")
            await pilot.pause(0.2)
            assert isinstance(app.screen, IdeaActions)
            await pilot.press("e")  # edit from the idea view
            await pilot.pause(0.2)
            await pilot.press("tab", "end", "ctrl+u", *"Soil and weather.", "ctrl+s")
            await settle(pilot, app)
            await pilot.press("enter")
            await pilot.pause(0.2)
            await pilot.press("t")  # start a project from the idea view
            await pilot.pause(0.2)
            assert isinstance(app.screen, StartProject)
            assert app.screen.query_one("#start-name").value == "garden-sensor"
            await pilot.press("enter")
            await settle(pilot, app, rounds=5)
            assert isinstance(app.screen, HomeScreen)
            assert not [c for c in items(app, "ideas") if isinstance(c, IdeaCard)]
            focused = app.screen.focused
            assert focused.id == "projects"
            assert focused.highlighted_child.project["name"] == "2026-09-26-garden-sensor"

    run(drive())
    assert (env["home"] / "Projects" / "tries" / "2026-09-26-garden-sensor").is_dir()
    shown = user.get(f"/v1/ideas/{idea['id']}")
    assert shown["idea"]["status"] == "started" and shown["idea"]["summary"] == \
        "Soil and weather."
    assert shown["project"]["key"] == "personal/tries/2026-09-26-garden-sensor"
    project = user.get("/v1/projects/personal/tries/2026-09-26-garden-sensor")
    assert project["project"]["description"] == "Soil and weather."
    assert project["idea"]["title"] == "Garden Sensor!"


def test_project_description_is_editable_from_home(env):
    async def drive():
        app = make_app(env)
        async with app.run_test(size=(160, 44)) as pilot:
            await settle(pilot, app)
            await pilot.press("2", "e")
            await pilot.pause(0.2)
            await pilot.press(*"GTFS-RT feeds.", "ctrl+s")
            await settle(pilot, app)
            assert "GTFS-RT feeds." in app.screen.details_markup

    run(drive())
    assert env["user"].get(f"/v1/projects/personal/{TRY}")["project"]["description"] == \
        "GTFS-RT feeds."


# --- Board -------------------------------------------------------------------------------


def test_board_create_move_pause_done_select_and_back(env):
    user = env["user"]

    async def drive():
        app = make_app(env)
        async with app.run_test(size=(170, 44)) as pilot:
            await settle(pilot, app)
            await pilot.press("2", "enter")
            await settle(pilot, app)
            assert isinstance(app.screen, BoardScreen)
            await pilot.press("n")
            await pilot.pause(0.2)
            await pilot.press(*"Parse feed", "ctrl+s")
            await settle(pilot, app)
            inbox = [c for c in items(app, "col-inbox") if isinstance(c, TaskCard)]
            assert [c.task_data["title"] for c in inbox] == ["Parse feed"]
            await pilot.press("right_square_bracket")  # → Active, focus follows
            await settle(pilot, app)
            assert app.screen.focused.id == "col-active"
            await pilot.press("z")  # pause: stays in the Active pane, under the divider
            await settle(pilot, app)
            active = items(app, "col-active")
            assert isinstance(active[0], Divider) and "paused 1" in str(active[0].query_one(
                "Label").render())
            assert active[1].task_data["column_name"] == "Paused"
            assert app.screen.focused.highlighted_child is active[1]
            await pilot.press("z")  # resume
            await settle(pilot, app)
            await pilot.press("s")  # select for time attribution
            await settle(pilot, app)
            await pilot.press("D")
            await settle(pilot, app)
            assert app.screen.focused.id == "col-done"
            done = app.screen.focused.highlighted_child.task_data
            assert done["done_at"] and "Done" in app.screen.details_markup
            await pilot.press("escape")
            await settle(pilot, app)
            assert isinstance(app.screen, HomeScreen)

    run(drive())
    task_id = user.get("/v1/tasks")["tasks"][0]["id"]
    history = [h["column"] for h in user.get(f"/v1/tasks/{task_id}")["task"]["column_history"]]
    assert history == ["Inbox", "Active", "Paused", "Active", "Done"]
    assert user.get("/v1/selection")["selections"][0]["task_title"] == "Parse feed"


def test_board_refuses_unexpected_columns(env, tmp_path, db_path):
    async def drive():
        app = make_app(env)
        async with app.run_test(size=(160, 44)) as pilot:
            await settle(pilot, app)
            app.columns = ["Todo", "Doing", "Done"]
            await pilot.press("2", "enter")
            await settle(pilot, app)
            assert "do not match" in app.screen.details_markup
            assert not [c for c in items(app, "col-inbox") if isinstance(c, TaskCard)]

    run(drive())


# --- layout, help, themes ---------------------------------------------------------------


def test_narrow_terminals_hide_details_behind_an_overlay(env):
    async def drive():
        app = make_app(env)
        async with app.run_test(size=(100, 40)) as pilot:
            await settle(pilot, app)
            assert app.screen.has_class("narrow") and not app.screen.has_class("single")
            assert app.screen.query_one("#details-pane").display is False or \
                app.screen.query_one("#details-pane").styles.display == "none"
            await pilot.press("v")
            await pilot.pause(0.2)
            assert isinstance(app.screen, DetailsOverlay)
            await pilot.press("escape")
            await pilot.resize_terminal(60, 30)
            await pilot.pause(0.3)
            home = app.screen
            assert home.has_class("single")
            shown = [p.id for p in home.query(".pane") if p.styles.display != "none"]
            assert shown == ["projects-pane"]
            await pilot.press("tab")
            await pilot.pause(0.2)
            shown = [p.id for p in home.query(".pane") if p.styles.display != "none"]
            assert shown == ["ideas-pane"]

    run(drive())


def test_help_lists_the_keys_of_the_current_screen(env):
    async def drive():
        app = make_app(env)
        async with app.run_test(size=(160, 44)) as pilot:
            await settle(pilot, app)
            await pilot.press("question_mark")
            await pilot.pause(0.2)
            assert isinstance(app.screen, HelpScreen)
            text = str(app.screen.query_one(".help-body Static").render())
            assert "Add idea" in text and "Start project" in text and "Theme" in text

    run(drive())


def test_theme_cycles_and_persists_per_device(env):
    async def drive():
        app = make_app(env)
        async with app.run_test(size=(160, 44)) as pilot:
            await settle(pilot, app)
            assert app.theme == "dark"
            await pilot.press("T")
            await pilot.pause(0.2)
            assert app.theme == "light"

    run(drive())
    assert theming.load_prefs(env["prefs"])["theme"] == "light"
    again = make_app(env)
    assert again.theme == "light"


def test_user_themes_add_and_override_and_bad_files_are_reported(tmp_path):
    user_dir = tmp_path / "themes"
    user_dir.mkdir()
    base = theming.load_themes(tmp_path / "none")[0]["amber"]
    colors = "\n".join(f'{k} = "{v}"' for k, v in base.colors.items())
    (user_dir / "mine.toml").write_text(
        f'name = "mine"\n[colors]\n{colors}\n[workspaces]\nfallback = "#888888"\n'
        f'hobby = "#ff00ff"\n')
    (user_dir / "amber.toml").write_text(
        f'name = "amber"\ndark = true\n[colors]\n{colors}\n[workspaces]\nfallback = "#123456"\n')
    (user_dir / "broken.toml").write_text('name = "broken"\n[colors]\naccent = "red"\n')
    themes, problems = theming.load_themes(user_dir)
    assert set(themes) == {"amber", "dark", "light", "phosphor", "mine"}
    assert themes["amber"].workspace_color("personal") == "#123456"  # override, fallback
    assert themes["mine"].workspace_color("hobby") == "#FF00FF"
    assert len(problems) == 1 and "missing" in problems[0]
    assert "hobby" in theming.workspace_names(themes)
    textual = theming.to_textual(themes["mine"], ["personal", "hobby"])
    assert textual.variables["ws-hobby"] == "#FF00FF" and "ws-personal-dim" in textual.variables


def test_every_bundled_theme_defines_every_configured_workspace():
    themes, problems = theming.load_themes()
    assert problems == [] and set(themes) >= {"dark", "light", "phosphor", "amber"}
    for spec in themes.values():
        assert {"personal", "work", "fallback"} <= set(spec.workspaces), spec.name
        assert spec.workspace_color("personal") != spec.workspace_color("work"), spec.name
