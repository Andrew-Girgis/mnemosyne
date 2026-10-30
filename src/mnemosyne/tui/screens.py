"""Home (Ideas | Projects | Details) and Board (Inbox | Active | Done | Details) screens."""

from rich.markup import escape
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, ListView

from mnemosyne.client import ClientError
from mnemosyne.tui.modals import (
    Confirm,
    DetailsOverlay,
    HelpScreen,
    IdeaActions,
    IdeaForm,
    StartProject,
    Suggestions,
    TaskForm,
    TextForm,
    TextPrompt,
)
from mnemosyne.tui.widgets import (
    AddItem,
    DetailsBody,
    Divider,
    EmptyNote,
    IdeaCard,
    Pane,
    ProjectCard,
    TaskCard,
    TopBar,
    fmt_date,
    fmt_duration,
    fmt_time,
    kv,
    tag_for,
)

NARROW = 110  # below this the Details pane becomes an overlay (v)
SINGLE = 70  # below this one pane shows at a time (tab / 1-4 switch)
LIFECYCLE_FILTERS = ("active", "paused", "archived", None)  # None = all
BOARD_ROLES = ("inbox", "active", "paused", "done")


def section(title: str) -> str:
    return f"\n[b $accent]{title}[/]"


def pick_index(items: list, id_of, keep: str | None, default: int | None) -> int | None:
    """Where the cursor goes in a rebuilt list: the kept item, else `default`."""
    for i, item in enumerate(items):
        if keep and id_of(item) == keep:
            return i
    return default if default is not None and default < len(items) else None


async def replace_items(view: ListView, items: list, index: int | None) -> None:
    """Swap a list's items. ListView.clear() removes asynchronously, so wait for it before
    adding and placing the cursor (otherwise indexes count the outgoing items)."""
    await view.clear()
    await view.extend(items)
    view.index = index


class PaneScreen(Screen):
    """Shared: top bar, responsive pane layout, pane focus keys, details, API calls."""

    PANES: tuple[str, ...] = ()  # list ids, in order (keys 1..n)

    def __init__(self):
        super().__init__()
        self.details_markup = ""

    # --- layout ---------------------------------------------------------------------

    def on_resize(self, event) -> None:
        width = event.size.width
        self.set_class(width < NARROW, "narrow")
        self.set_class(width < SINGLE, "single")
        self._mark_current_pane()
        if self.is_mounted and self.query(TopBar):
            self.refresh_topbar()

    def on_descendant_focus(self, _event) -> None:
        self._mark_current_pane()

    def _mark_current_pane(self) -> None:
        focused = self.focused
        for pane in self.query(Pane):
            pane.set_class(focused is not None and focused.id == pane.list_id, "current")

    def action_focus_pane(self, n: int) -> None:
        if n < len(self.PANES):
            self.query_one(f"#{self.PANES[n]}", ListView).focus()

    def action_cycle_pane(self, step: int) -> None:
        focused = self.focused.id if self.focused else None
        i = self.PANES.index(focused) if focused in self.PANES else -1
        self.action_focus_pane((i + step) % len(self.PANES))

    # --- details --------------------------------------------------------------------

    def show_details(self, markup: str) -> None:
        self.details_markup = markup
        self.query_one("#details-body").update(markup)

    def action_details_overlay(self) -> None:
        if self.details_markup:
            self.app.push_screen(DetailsOverlay(self.details_markup))

    def action_help(self) -> None:
        def keys(bindings) -> list[tuple[str, str]]:
            out = []
            for b in bindings:
                if isinstance(b, Binding) and b.description:
                    key = b.key_display or b.key.split(",")[0]
                    out.append((key, b.description))
            return out
        self.app.push_screen(HelpScreen([(self.HELP_TITLE, keys(self.BINDINGS)),
                                         ("Everywhere", keys(self.app.BINDINGS))]))

    # --- API ------------------------------------------------------------------------

    def call(self, fn, *args, **kwargs):
        """Run an API call from a worker thread; report errors as notifications."""
        try:
            return fn(*args, **kwargs)
        except ClientError as err:
            self.app.call_from_thread(self.app.notify, err.message, title=err.code,
                                      severity="error")
            return None

    def refresh_topbar(self) -> None:
        app = self.app
        sel = app.my_selection
        if sel and sel.get("task_title"):
            focus = (f"[$accent]◉[/] {escape(sel['task_title'])}"
                     f" [$muted]({escape(sel['project_key'])})[/]")
        elif sel and sel.get("project_key"):
            focus = f"[$accent]◉[/] {escape(sel['project_key'])}"
        else:
            focus = "[$muted]no selection · time unassigned[/]"
        width = self.size.width
        if width < SINGLE:  # room for the screen name only; the selection marker stays
            right = "[$accent]◉[/]" if sel and sel.get("project_key") else ""
        elif width < NARROW:
            right = focus
        else:
            right = f"{focus}  [$muted]│ {app.device or '…'} │ {app.theme}[/]"
        self.query_one(TopBar).set(self.topbar_left(), right)

    def topbar_left(self) -> str:
        return "[b $accent]MNEMOSYNE[/]"


# ============================================================================================


class HomeScreen(PaneScreen):
    PANES = ("ideas", "projects")
    HELP_TITLE = "Home"
    BINDINGS = [
        Binding("question_mark", "help", "Help", key_display="?"),
        Binding("enter", "open", "Open", show=False),
        Binding("i", "add_idea", "Add idea"),
        Binding("e", "edit", "Edit"),
        Binding("t", "start_project", "Start project"),
        Binding("d", "drop_idea", "Drop idea", show=False),
        Binding("p", "new_project", "New project", show=False),
        Binding("L", "cycle_lifecycle", "Project lifecycle", show=False),
        Binding("w", "cycle_workspace", "Workspace filter"),
        Binding("f", "cycle_filter", "Lifecycle filter", show=False),
        Binding("s", "select", "Select project", show=False),
        Binding("x", "clear_selection", "Clear selection", show=False),
        Binding("m", "suggestions", "Move suggestions", show=False),
        Binding("v", "details_overlay", "Details (narrow)", show=False),
        Binding("1", "focus_pane(0)", "Ideas pane", show=False),
        Binding("2", "focus_pane(1)", "Projects pane", show=False),
        Binding("tab", "cycle_pane(1)", "Next pane", show=False, priority=True),
        Binding("shift+tab", "cycle_pane(-1)", "Previous pane", show=False, priority=True),
    ]

    def __init__(self):
        super().__init__()
        self.ideas: list[dict] = []
        self.projects: list[dict] = []
        self.boards: dict[str, dict] = {}  # project id → board, for Details
        self.keep_project: str | None = None  # id to re-highlight after a reload
        self.keep_idea: str | None = None

    def compose(self) -> ComposeResult:
        yield TopBar(id="topbar")
        with Horizontal(id="panes"):
            yield Pane("IDEAS", "1", "ideas", id="ideas-pane")
            yield Pane("PROJECTS", "2", "projects", id="projects-pane")
            with VerticalScroll(id="details-pane", classes="pane details") as details:
                details.border_title = "DETAILS"
                yield DetailsBody(id="details-body")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#projects", ListView).focus()
        self.reload()

    def topbar_left(self) -> str:
        app = self.app
        filters = f"{app.workspace_filter or 'all workspaces'} · {app.lifecycle_filter or 'all'}"
        return f"[b $accent]MNEMOSYNE[/] [$muted]▸[/] [b]HOME[/]  [$muted]{filters}[/]"

    # --- loading --------------------------------------------------------------------

    def reload(self) -> None:
        self.load()

    @work(thread=True, exclusive=True, group="home")
    def load(self) -> None:
        app = self.app
        ideas = self.call(app.client.get, "/v1/ideas", workspace=app.workspace_filter)
        projects = self.call(app.client.get, "/v1/projects", workspace=app.workspace_filter,
                             lifecycle=app.lifecycle_filter)
        app.load_selection_sync()
        if ideas is None or projects is None:
            return
        self.app.call_from_thread(self._show, ideas["ideas"], projects["projects"])

    async def _show(self, ideas: list[dict], projects: list[dict]) -> None:
        app = self.app
        self.boards.clear()
        projects.sort(key=lambda p: -(p.get("last_activity_at") or p.get("started_at") or 0))
        self.ideas, self.projects = ideas, projects
        known = set(app.workspaces)
        sel_project = (app.my_selection or {}).get("project_id")

        view = self.query_one("#ideas", ListView)
        keep = self.keep_idea or self._current_id(view, "idea")
        cards = [AddItem("+ Add idea"), *(IdeaCard(idea, known) for idea in ideas)]
        if not ideas:
            cards.append(EmptyNote("No open ideas. Press i to add one."))
        index = pick_index(cards, lambda c: getattr(c, "idea", {}).get("id"), keep,
                           default=1 if ideas else 0)
        await replace_items(view, cards, index)
        self.query_one("#ideas-pane", Pane).set_count(len(ideas))

        view = self.query_one("#projects", ListView)
        keep = self.keep_project or self._current_id(view, "project")
        cards = [ProjectCard(p, known, app.tz, p["id"] == sel_project) for p in projects]
        if not projects:
            cards.append(EmptyNote("No projects match the filters (w / f)."))
        index = pick_index(cards, lambda c: getattr(c, "project", {}).get("id"), keep,
                           default=0 if projects else None)
        await replace_items(view, cards, index)
        self.query_one("#projects-pane", Pane).set_count(len(projects))
        self.keep_idea = self.keep_project = None
        self.refresh_topbar()
        self._details_for_focus()

    @staticmethod
    def _current_id(view: ListView, attr: str) -> str | None:
        item = view.highlighted_child
        data = getattr(item, attr, None) if item is not None else None
        return data["id"] if data else None

    # --- details --------------------------------------------------------------------

    @on(ListView.Highlighted)
    def _highlighted(self, event: ListView.Highlighted) -> None:
        if event.list_view.has_focus or not self.details_markup:
            self._details_for(event.item)

    def on_descendant_focus(self, event) -> None:
        super().on_descendant_focus(event)
        self._details_for_focus()

    def _details_for_focus(self) -> None:
        focused = self.focused
        if isinstance(focused, ListView):
            self._details_for(focused.highlighted_child)

    def _details_for(self, item) -> None:
        if isinstance(item, IdeaCard):
            self.show_details(self._idea_details(item.idea))
        elif isinstance(item, ProjectCard):
            board = self.boards.get(item.project["id"])
            self.show_details(self._project_details(item.project, board))
            if board is None:
                self.load_board(item.project["id"])
        elif isinstance(item, AddItem):
            self.show_details("[b $accent]+ Add idea[/]\n\n[$muted]Enter or i: capture a new"
                              " idea. Open an idea (enter) to edit it or start a project from"
                              " it with try.[/]")

    @work(thread=True, exclusive=True, group="details")
    def load_board(self, project_id: str) -> None:
        board = self.call(self.app.client.get, f"/v1/projects/{project_id}/board")
        if board is not None:
            self.app.call_from_thread(self._board_loaded, project_id, board)

    def _board_loaded(self, project_id: str, board: dict) -> None:
        self.boards[project_id] = board
        item = self.query_one("#projects", ListView).highlighted_child
        if isinstance(item, ProjectCard) and item.project["id"] == project_id:
            self._details_for(item)

    def _idea_details(self, i: dict) -> str:
        tz = self.app.tz
        lines = [f"[b $accent]{escape(i['title'])}[/]",
                 f"[$muted]{i['id']}[/]", "",
                 kv("Workspace", f"{i['workspace']} [$muted]({tag_for(i['workspace'])})[/]", 10),
                 kv("Status", i["status"], 10),
                 kv("Created", fmt_time(i["created_at"], tz), 10),
                 kv("Updated", fmt_time(i["updated_at"], tz), 10)]
        if i.get("summary"):
            lines += [section("Summary"), escape(i["summary"])]
        lines += [section("Notes"), escape(i["body"]) if i.get("body", "").strip()
                  else "[$muted]none[/]"]
        lines += ["", "[$muted]enter open · e edit · t start project with try · d drop[/]"]
        return "\n".join(lines)

    def _project_details(self, p: dict, board: dict | None) -> str:
        tz = self.app.tz
        lines = [f"[b $accent]{escape(p['name'])}[/]", f"[$muted]{escape(p['key'])}[/]", "",
                 kv("Started", f"{fmt_date(p.get('started_at'), tz)}"
                               f" [$muted]({p.get('started_source', '?')})[/]", 10),
                 kv("Time", f"{fmt_duration(p.get('any_seconds'))} [$muted](you"
                            f" {fmt_duration(p.get('editor_seconds'))} · agents"
                            f" {fmt_duration(p.get('agent_seconds'))})[/]", 10),
                 kv("Status", f"{p['lifecycle']} · {p['kind']} · {p['workspace']}", 10)]
        if p.get("last_activity_at"):
            lines.append(kv("Last seen", fmt_time(p["last_activity_at"], tz), 10))
        if board and board.get("idea"):
            lines.append(kv("From idea", f"◆ {escape(board['idea']['title'])}", 10))
        lines += [section("Description"),
                  escape(p["description"]) if p.get("description") else
                  "[$muted]none · e to write one[/]"]
        if board is None:
            lines += ["", "[$muted]loading…[/]"]
            return "\n".join(lines)
        lines += [section("Coding time"),
                  f"[$muted]{'':10}{'you':>7}{'agents':>11}{'either':>11}[/]"]
        for label in ("today", "week", "all"):
            c = board["coding"][label]
            lines.append(f"  {label:<8}{fmt_duration(c['editor_seconds']):>7}"
                         f"{fmt_duration(c['agent_seconds']):>11}"
                         f"{fmt_duration(c['any_seconds']):>11}")
        cols = {name.lower(): tasks for name, tasks in board["columns"].items()}
        current = cols.get("active", []) + cols.get("paused", [])
        counts = " · ".join(f"{name} {len(tasks)}" for name, tasks in board["columns"].items())
        lines += [section("Current tasks"), f"[$muted]{counts}[/]"]
        for t in current:
            mark = "[$warning]‖[/]" if t["column_name"].lower() == "paused" else "[$success]▶[/]"
            lines.append(f"  {mark} {escape(t['title'])}")
        if not current:
            lines.append("  [$muted]nothing active[/]")
        lines.append(section("Recent commits"))
        for commit in board["commits"][:6]:
            lines.append(f"  [$muted]{fmt_date(commit['committed_at'], tz)}[/]"
                         f" {escape((commit['subject'] or '')[:70])}")
        if not board["commits"]:
            lines.append("  [$muted]none seen[/]")
        lines += ["", "[$muted]enter open board · e edit description · s select[/]"]
        return "\n".join(lines)

    # --- helpers --------------------------------------------------------------------

    def _focused_item(self):
        focused = self.focused
        return focused.highlighted_child if isinstance(focused, ListView) else None

    def _idea(self) -> dict | None:
        item = self._focused_item()
        return item.idea if isinstance(item, IdeaCard) else None

    def _project(self) -> dict | None:
        item = self._focused_item()
        return item.project if isinstance(item, ProjectCard) else None

    @work(thread=True)
    def mutate(self, fn, path: str, body: dict | None = None, done=None) -> None:
        result = self.call(fn, path, body) if body is not None else self.call(fn, path)
        if result is not None:
            if done:
                self.app.call_from_thread(done, result)
            self.app.call_from_thread(self.reload)

    # --- actions --------------------------------------------------------------------

    def action_open(self) -> None:
        item = self._focused_item()
        if isinstance(item, AddItem):
            self.action_add_idea()
        elif isinstance(item, IdeaCard):
            def chosen(choice: str | None) -> None:
                if choice == "edit":
                    self.action_edit()
                elif choice == "start":
                    self.action_start_project()
                elif choice == "drop":
                    self.action_drop_idea()
            self.app.push_screen(IdeaActions(item.idea), chosen)
        elif isinstance(item, ProjectCard):
            self.app.push_screen(BoardScreen(item.project))

    @on(ListView.Selected)
    def _selected(self, event: ListView.Selected) -> None:
        event.stop()
        self.action_open()

    def action_add_idea(self) -> None:
        app = self.app

        def create(result: dict | None) -> None:
            if result:
                self.mutate(app.client.post, "/v1/ideas", result,
                            done=lambda r: setattr(self, "keep_idea", r["idea"]["id"]))
                self.query_one("#ideas", ListView).focus()
        self.app.push_screen(IdeaForm("New idea", app.workspaces,
                                      default_workspace=app.workspace_filter), create)

    def action_edit(self) -> None:
        idea, project = self._idea(), self._project()
        if idea:
            def save(result: dict | None) -> None:
                if result:
                    self.mutate(self.app.client.patch, f"/v1/ideas/{idea['id']}", result)
            self.app.push_screen(IdeaForm("Edit idea", self.app.workspaces, idea), save)
        elif project:
            def describe(result: dict | None) -> None:
                if result is not None:
                    self.boards.pop(project["id"], None)
                    self.mutate(self.app.client.patch, f"/v1/projects/{project['id']}",
                                {"description": result["text"]})
            self.app.push_screen(TextForm(f"Description · {project['name']}",
                                          project.get("description") or ""), describe)

    def action_drop_idea(self) -> None:
        idea = self._idea()
        if not idea:
            return

        def drop(yes: bool) -> None:
            if yes:
                self.mutate(self.app.client.post, f"/v1/ideas/{idea['id']}/drop")
        self.app.push_screen(Confirm(f"Drop “{idea['title']}”?",
                                     "It leaves the Ideas pane; `mnemosyne ideas reopen` brings"
                                     " it back."), drop)

    def action_start_project(self) -> None:
        idea = self._idea()
        if not idea:
            self.notify("Highlight an idea first (Ideas pane)", severity="warning")
            return
        from mnemosyne import trystart

        roots = {w["name"]: w["try_root"] for w in self.app.scan_roots}
        if not roots:
            self.notify("This device has no Try roots configured", severity="error")
            return

        def start(result: dict | None) -> None:
            if result:
                self.notify(f"Creating {result['name']} with try…")
                self.run_start(idea, result["workspace"], result["name"])
        self.app.push_screen(StartProject(idea, list(roots), roots,
                                          trystart.slugify(idea["title"])), start)

    @work(thread=True, exclusive=True, group="start")
    def run_start(self, idea: dict, workspace: str, name: str) -> None:
        from mnemosyne import trystart

        out = self.call(trystart.start_idea, self.app.client, idea["id"], workspace, name)
        if out is None:
            return
        self.keep_project = out["project"]["id"]
        path = out["path"]
        self.app.call_from_thread(self.app.copy_to_clipboard, f"cd {path}")
        self.app.call_from_thread(
            self.notify, f"{path}\n`cd` command copied to the clipboard.",
            title=f"Started {out['project']['name']}", timeout=12)
        self.app.call_from_thread(self._after_start)

    def _after_start(self) -> None:
        self.reload()
        self.query_one("#projects", ListView).focus()

    def action_new_project(self) -> None:
        workspace = self.app.workspace_filter or "personal"

        def create(name: str | None) -> None:
            if name:
                self.mutate(self.app.client.post, "/v1/projects",
                            {"name": name, "workspace": workspace},
                            done=lambda r: setattr(self, "keep_project", r["project"]["id"]))
        self.app.push_screen(TextPrompt(f"New project in {workspace} (no folder)"), create)

    def action_cycle_lifecycle(self) -> None:
        project = self._project()
        if not project:
            return
        order = ("active", "paused", "archived")
        nxt = order[(order.index(project["lifecycle"]) + 1) % len(order)]

        def change(yes: bool) -> None:
            if yes:
                self.mutate(self.app.client.patch, f"/v1/projects/{project['id']}",
                            {"lifecycle": nxt})
        self.app.push_screen(Confirm(f"Mark {project['name']} as {nxt}?"), change)

    def action_cycle_workspace(self) -> None:
        options = [None, *self.app.workspaces]
        i = options.index(self.app.workspace_filter) if self.app.workspace_filter in options \
            else 0
        self.app.workspace_filter = options[(i + 1) % len(options)]
        self.reload()

    def action_cycle_filter(self) -> None:
        i = LIFECYCLE_FILTERS.index(self.app.lifecycle_filter)
        self.app.lifecycle_filter = LIFECYCLE_FILTERS[(i + 1) % len(LIFECYCLE_FILTERS)]
        self.reload()

    def action_select(self) -> None:
        project = self._project()
        if project:
            self.mutate(self.app.client.put, "/v1/selection", {"project": project["id"]})

    def action_clear_selection(self) -> None:
        self.mutate(self.app.client.delete, "/v1/selection")

    def action_suggestions(self) -> None:
        self.app.push_screen(Suggestions(self.app.client), lambda _: self.reload())


# ============================================================================================


class BoardScreen(PaneScreen):
    PANES = ("col-inbox", "col-active", "col-done")
    HELP_TITLE = "Project board"
    MOVE_ORDER = ("inbox", "active", "done")
    BINDINGS = [
        Binding("question_mark", "help", "Help", key_display="?"),
        Binding("escape,backspace", "back", "Back to Home", key_display="esc"),
        Binding("n", "new_task", "New task"),
        Binding("e", "edit_task", "Edit"),
        Binding("a", "note", "Note", show=False),
        Binding("left_square_bracket", "move(-1)", "◀ Move", key_display="["),
        Binding("right_square_bracket", "move(1)", "Move ▶", key_display="]"),
        Binding("z", "toggle_pause", "Pause/resume"),
        Binding("D", "done", "Done"),
        Binding("s", "select", "Select task", show=False),
        Binding("x", "clear_selection", "Clear selection", show=False),
        Binding("d", "archive", "Archive task", show=False),
        Binding("v", "details_overlay", "Details (narrow)", show=False),
        Binding("1", "focus_pane(0)", "Inbox pane", show=False),
        Binding("2", "focus_pane(1)", "Active pane", show=False),
        Binding("3", "focus_pane(2)", "Done pane", show=False),
        Binding("tab", "cycle_pane(1)", "Next pane", show=False, priority=True),
        Binding("shift+tab", "cycle_pane(-1)", "Previous pane", show=False, priority=True),
    ]

    def __init__(self, project: dict):
        super().__init__()
        self.project = project
        self.board: dict | None = None
        self.columns: dict[str, str] = {}  # role → configured column name
        self.focus_after: tuple[str, str] | None = None  # (role, task id) after a reload

    def compose(self) -> ComposeResult:
        yield TopBar(id="topbar")
        with Horizontal(id="panes"):
            yield Pane("INBOX", "1", "col-inbox", id="pane-inbox")
            yield Pane("ACTIVE", "2", "col-active", id="pane-active")
            yield Pane("DONE", "3", "col-done", id="pane-done")
            with VerticalScroll(id="details-pane", classes="pane details") as details:
                details.border_title = "DETAILS"
                yield DetailsBody(id="details-body")
        yield Footer()

    def on_mount(self) -> None:
        names = {c.lower(): c for c in self.app.columns}
        if set(names) != set(BOARD_ROLES):
            self.show_details(
                "[b $error]Board columns do not match[/]\n\nThis board needs exactly the"
                f" columns Inbox, Active, Paused and Done; the server has"
                f" {', '.join(self.app.columns)}. Nothing was changed.")
            return
        self.columns = names
        self.query_one("#col-inbox", ListView).focus()
        self.reload()

    def topbar_left(self) -> str:
        p = self.project
        return (f"[b $accent]MNEMOSYNE[/] [$muted]▸[/] [b]{escape(p['name'])}[/]"
                f"  [$muted]{tag_for(p['workspace'])} · {fmt_duration(p.get('any_seconds'))}"
                f" · esc back[/]")

    def action_back(self) -> None:
        self.app.pop_screen()
        self.app.screen.reload()

    def reload(self) -> None:
        self.load()

    # --- loading --------------------------------------------------------------------

    @work(thread=True, exclusive=True, group="board")
    def load(self) -> None:
        data = self.call(self.app.client.get, f"/v1/projects/{self.project['id']}/board")
        self.app.load_selection_sync()
        if data is not None:
            self.app.call_from_thread(self._show, data)

    async def _show(self, data: dict) -> None:
        app = self.app
        self.board = data
        self.project = {**self.project, **data["project"]}
        by_role = {role: data["columns"].get(name, []) for role, name in self.columns.items()}
        sel_task = (app.my_selection or {}).get("task_id")
        known, ws = set(app.workspaces), self.project["workspace"]
        target_role, target_id = self.focus_after or (None, None)
        for role in self.MOVE_ORDER:
            view = self.query_one(f"#col-{role}", ListView)
            keep = target_id if role == target_role else self._current_id(view)
            tasks = by_role[role]
            cards = [TaskCard(t, ws, known, app.tz, t["id"] == sel_task) for t in tasks]
            count = f"{len(tasks)}"
            if role == "active" and by_role["paused"]:
                paused = by_role["paused"]
                cards.append(Divider(f"── paused {len(paused)} ──"))
                cards += [TaskCard(t, ws, known, app.tz, t["id"] == sel_task) for t in paused]
                count += f" · ‖ {len(paused)}"
            if not cards:
                cards.append(EmptyNote({"inbox": "Empty. n adds a task.",
                                        "active": "Nothing in progress.",
                                        "done": "Nothing finished yet."}[role]))
            first = next((i for i, c in enumerate(cards) if isinstance(c, TaskCard)), None)
            index = pick_index(cards, lambda c: getattr(c, "task_data", {}).get("id"), keep,
                               default=first)
            await replace_items(view, cards, index)
            self.query_one(f"#pane-{role}", Pane).set_count(count)
        if target_role:
            self.query_one(f"#col-{target_role}", ListView).focus()
        self.focus_after = None
        self.refresh_topbar()
        self._details_for_focus()

    @staticmethod
    def _current_id(view: ListView) -> str | None:
        item = view.highlighted_child
        return item.task_data["id"] if isinstance(item, TaskCard) else None

    # --- details --------------------------------------------------------------------

    @on(ListView.Highlighted)
    def _highlighted(self, event: ListView.Highlighted) -> None:
        if event.list_view.has_focus:
            self._details_for(event.item)

    def on_descendant_focus(self, event) -> None:
        super().on_descendant_focus(event)
        self._details_for_focus()

    def _details_for_focus(self) -> None:
        focused = self.focused
        self._details_for(focused.highlighted_child if isinstance(focused, ListView) else None)

    def _details_for(self, item) -> None:
        if isinstance(item, TaskCard):
            self.show_details(self._task_summary(item.task_data))
            self.load_task(item.task_data["id"])
        elif self.board is not None:
            self.show_details(self._project_summary())

    @work(thread=True, exclusive=True, group="details")
    def load_task(self, task_id: str) -> None:
        data = self.call(self.app.client.get, f"/v1/tasks/{task_id}")
        if data is not None:
            self.app.call_from_thread(self._task_loaded, data)

    def _task_loaded(self, data: dict) -> None:
        current = self._current_task()
        if current and current["id"] == data["task"]["id"]:
            self.show_details(self._task_details(data))

    def _task_head(self, t: dict) -> list[str]:
        tz = self.app.tz
        role = t["column_name"].lower()
        status = {"paused": "[$warning]PAUSED[/]", "done": "[$success]Done[/]",
                  "active": "[$accent]Active[/]"}.get(role, t["column_name"])
        lines = [f"[b $accent]{escape(t['title'])}[/]", f"[$muted]{t['id']}[/]", "",
                 kv("Status", status, 9),
                 kv("Project", escape(self.project["name"]), 9),
                 kv("Created", fmt_time(t["created_at"], tz), 9)]
        if t.get("started_at"):
            lines.append(kv("Started", fmt_time(t["started_at"], tz), 9))
        if t.get("paused_at"):
            lines.append(kv("Paused", fmt_time(t["paused_at"], tz), 9))
        if t.get("done_at"):
            lines.append(kv("Done", fmt_time(t["done_at"], tz), 9))
        lines.append(kv("Time", "[$muted]not tracked per task yet[/]", 9))
        lines += [section("Description"),
                  escape(t["description"]) if t.get("description") else "[$muted]none[/]"]
        return lines

    def _task_summary(self, t: dict) -> str:
        return "\n".join([*self._task_head(t), "", "[$muted]loading…[/]"])

    def _task_details(self, data: dict) -> str:
        tz = self.app.tz
        t = data["task"]
        lines = self._task_head(t)
        history = t.get("column_history") or []
        if history:
            lines.append(section("Timeline"))
            lines += [f"  [$muted]{fmt_time(h['at'], tz)}[/] → {escape(h['column'])}"
                      for h in history]
        lines.append(section("Notes"))
        for note in data["notes"]:
            who = note["actor_harness"] or note["actor_kind"]
            lines.append(f"  [$muted]{fmt_time(note['created_at'], tz)} {escape(who)}[/]")
            lines.append(f"  {escape(note['body'])}")
        if not data["notes"]:
            lines.append("  [$muted]none · a adds one[/]")
        lines.append(section("Agent sessions"))
        for s in data["sessions"]:
            lines.append(f"  {escape(s['harness'])} [$muted]{escape(s['session_id'][:12])}"
                         f" {fmt_time(s['at'], tz)}[/]")
        if not data["sessions"]:
            lines.append("  [$muted]none linked[/]")
        lines.append(section("History"))
        for h in data["history"][-12:]:
            who = h["actor_harness"] or h["actor_kind"]
            lines.append(f"  [$muted]{fmt_time(h['at'], tz)}[/] {h['op']}"
                         f" [$muted]{escape(who)}[/]")
        return "\n".join(lines)

    def _project_summary(self) -> str:
        p, board, tz = self.project, self.board, self.app.tz
        lines = [f"[b $accent]{escape(p['name'])}[/]", f"[$muted]{escape(p['key'])}[/]", "",
                 kv("Started", f"{fmt_date(p.get('started_at'), tz)}"
                               f" [$muted]({p.get('started_source', '?')})[/]", 9),
                 kv("Status", f"{p['lifecycle']} · {p['workspace']}", 9)]
        lines += [section("Coding time"),
                  f"[$muted]{'':10}{'you':>7}{'agents':>11}{'either':>11}[/]"]
        for label in ("today", "week", "all"):
            c = board["coding"][label]
            lines.append(f"  {label:<8}{fmt_duration(c['editor_seconds']):>7}"
                         f"{fmt_duration(c['agent_seconds']):>11}"
                         f"{fmt_duration(c['any_seconds']):>11}")
        lines += ["", "[$muted]n adds a task to the focused pane[/]"]
        return "\n".join(lines)

    # --- actions --------------------------------------------------------------------

    def _current_task(self) -> dict | None:
        focused = self.focused
        if isinstance(focused, ListView) and isinstance(focused.highlighted_child, TaskCard):
            return focused.highlighted_child.task_data
        return None

    def _focused_role(self) -> str:
        focused = self.focused
        if isinstance(focused, ListView) and (focused.id or "").startswith("col-"):
            return focused.id.removeprefix("col-")
        return "inbox"

    @work(thread=True)
    def mutate(self, fn, path: str, body: dict | None = None,
               focus: tuple[str, str] | None = None, done=None) -> None:
        result = self.call(fn, path, body) if body is not None else self.call(fn, path)
        if result is not None:
            if done:
                focus = done(result)
            self.focus_after = focus
            self.app.call_from_thread(self.reload)

    def _move_to(self, task: dict, role: str) -> None:
        self.mutate(self.app.client.post, f"/v1/tasks/{task['id']}/move",
                    {"column": self.columns[role]},
                    focus=("active" if role == "paused" else role, task["id"]))

    def action_new_task(self) -> None:
        if not self.columns:
            return
        role = self._focused_role()
        column = self.columns[role]

        def create(result: dict | None) -> None:
            if result:
                self.mutate(self.app.client.post, "/v1/tasks",
                            {"project": self.project["id"], "column": column, **result},
                            done=lambda r: (role, r["task"]["id"]))
        self.app.push_screen(TaskForm(f"New task · {self.project['name']} · {column}"), create)

    def action_edit_task(self) -> None:
        task = self._current_task()
        if not task:
            return

        def save(result: dict | None) -> None:
            if result:
                self.mutate(self.app.client.patch, f"/v1/tasks/{task['id']}", result)
        self.app.push_screen(TaskForm("Edit task", task["title"], task["description"]), save)

    def action_note(self) -> None:
        task = self._current_task()
        if not task:
            return

        def save(body: str | None) -> None:
            if body:
                self.mutate(self.app.client.post, f"/v1/tasks/{task['id']}/notes",
                            {"body": body})
        self.app.push_screen(TextPrompt(f"Note on: {task['title']}"), save)

    def action_move(self, step: int) -> None:
        task = self._current_task()
        if not task:
            return
        role = task["column_name"].lower()
        position = self.MOVE_ORDER.index("active" if role == "paused" else role)
        target = position + step
        if 0 <= target < len(self.MOVE_ORDER):
            self._move_to(task, self.MOVE_ORDER[target])

    def action_toggle_pause(self) -> None:
        task = self._current_task()
        if not task:
            return
        role = task["column_name"].lower()
        if role == "active":
            self._move_to(task, "paused")
        elif role == "paused":
            self._move_to(task, "active")
        else:
            self.notify("Only active tasks can be paused (] starts it first)",
                        severity="warning")

    def action_done(self) -> None:
        task = self._current_task()
        if task and task["column_name"].lower() != "done":
            self._move_to(task, "done")

    def action_select(self) -> None:
        task = self._current_task()
        if task:
            self.mutate(self.app.client.put, "/v1/selection", {"task": task["id"]})
        else:
            self.mutate(self.app.client.put, "/v1/selection", {"project": self.project["id"]})

    def action_clear_selection(self) -> None:
        self.mutate(self.app.client.delete, "/v1/selection")

    def action_archive(self) -> None:
        task = self._current_task()
        if not task:
            return

        def archive(yes: bool) -> None:
            if yes:
                self.mutate(self.app.client.post, f"/v1/tasks/{task['id']}/archive")
        self.app.push_screen(Confirm(f"Archive “{task['title']}”?",
                                     "`mnemosyne tasks unarchive` restores it."), archive)
