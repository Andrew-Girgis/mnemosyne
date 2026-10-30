"""Keyboard-driven TUI: Home (Ideas | Projects | Details) and per-project boards.
Uses the same API operations as the CLI, as the user."""

import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from textual import work
from textual.app import App
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Label

from mnemosyne.client import Client, ClientError
from mnemosyne.tui import theming
from mnemosyne.tui.screens import HomeScreen, PaneScreen
from mnemosyne.tui.widgets import workspace_css

REFRESH_SECONDS = 30

BASE_CSS = """
Screen { background: $background; color: $foreground; layout: vertical; }

#topbar { height: 1; padding: 0 1; background: $surface; }
#topbar-left { width: 1fr; }
#topbar-right { width: auto; }
Footer { background: $surface; }

#panes { height: 1fr; padding: 0 0 0 0; }
.pane {
    width: 1fr; height: 1fr; margin: 0 0 0 1;
    border: round $faint; border-title-color: $muted; border-title-style: bold;
    border-subtitle-color: $faint; border-subtitle-align: right;
    background: $surface; padding: 0 1;
}
.pane:focus-within { border: round $accent; border-title-color: $accent; }
.pane.details { width: 1.3fr; margin-right: 1; padding: 0 2; }
#projects-pane { width: 1.2fr; }
#details-body { width: 1fr; }

.narrow .pane.details { display: none; }
.narrow .pane { margin-right: 0; }
.narrow #panes > .pane:last-of-type { margin-right: 1; }
.single .pane { display: none; }
.single .pane.current { display: block; margin-right: 1; }

ListView, ListView:focus { height: 1fr; background: $surface; background-tint: $foreground 0%; }
ListView > ListItem { background: $surface; }
ListView > ListItem.-highlight, ListView:focus > ListItem.-highlight {
    background: $surface; color: $foreground; text-style: none;
}

/* Cards share the pane background: a filled card would show half a cell past its border
   (terminal borders sit mid-cell). Selection = heavy workspace-coloured border + accent title. */
.card { height: auto; padding: 0 1; margin: 1 0 0 0; background: $surface; }
.card-head { height: 1; }
.card-dot { width: 2; }
.card-title { width: 1fr; text-style: bold; text-wrap: nowrap; text-overflow: ellipsis; }
.card-tag { width: auto; margin-left: 1; text-style: bold; }
.card-line { width: 1fr; text-wrap: nowrap; text-overflow: ellipsis; color: $foreground; }
ListView:focus > .card.-highlight .card-title { color: $accent; }
.card.selected .card-dot { text-style: bold; }
.card.done .card-title { color: $muted; text-style: none; }
.card.paused .card-dot { color: $warning; }

.add-item { height: 3; margin: 1 0 0 0; padding: 0 1; border: dashed $faint; color: $muted; }
ListView:focus > .add-item.-highlight { border: dashed $accent; color: $accent; }
.divider { height: 1; margin: 1 0 0 0; color: $muted; }
.empty-note { height: auto; margin: 1 0 0 0; color: $muted; }

.dialog {
    width: 76; max-width: 95%; height: auto; max-height: 90%;
    padding: 1 2; border: round $accent; background: $panel;
}
.dialog.wide { width: 100; height: 80%; }
.dialog-title { text-style: bold; color: $accent; margin-bottom: 1; }
.dialog-text { margin-bottom: 1; }
.field-label { color: $muted; margin-top: 1; }
.hint { color: $muted; margin-top: 1; }
.choices { height: auto; margin: 1 0 0 0; }
.choice { margin-right: 4; }
.help-body { height: 1fr; }
#task-description, #idea-body { height: 6; }
TextArea.tall { height: 12; }
ModalScreen { align: center middle; background: $background 60%; }
"""


class MnemosyneApp(App):
    TITLE = "Mnemosyne"
    # The palette would offer Textual's own themes, which lack our workspace colours; `T`
    # cycles ours and `?` lists every key.
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("T", "cycle_theme", "Theme"),
        Binding("question_mark", "help", "Help", key_display="?", show=False),
        Binding("r", "refresh", "Refresh", show=False),
    ]

    def __init__(self, client: Client, *, prefs_path: Path | None = None,
                 themes_dir: Path | None = None):
        self.theme_specs, problems = theming.load_themes(themes_dir)
        self.prefs_path = prefs_path
        self.ws_names = theming.workspace_names(self.theme_specs, ["personal", "work"])
        self.CSS = BASE_CSS + workspace_css(self.ws_names)
        super().__init__()
        for spec in self.theme_specs.values():
            self.register_theme(theming.to_textual(spec, self.ws_names))
        wanted = theming.load_prefs(prefs_path).get("theme", theming.DEFAULT_THEME)
        self.theme = wanted if wanted in self.theme_specs else theming.DEFAULT_THEME
        self.problems = problems
        self.client = client
        self.columns: list[str] = []
        self.workspaces: list[str] = ["personal", "work"]
        self.workspace_filter: str | None = None
        self.lifecycle_filter: str | None = "active"
        self.scan_roots: list[dict] = []
        self.device: str | None = None
        self.my_selection: dict | None = None
        self.tz = ZoneInfo("America/Toronto")

    def get_theme_variable_defaults(self) -> dict[str, str]:
        # Only reached if a theme without our tokens is ever active; keeps the CSS parseable.
        spec = self.theme_specs[theming.DEFAULT_THEME]
        return theming.to_textual(spec, self.ws_names).variables

    def on_mount(self) -> None:
        for problem in self.problems:
            self.notify(problem, title="Theme file skipped", severity="warning", timeout=10)
        self.push_screen(_Loading())
        self.bootstrap()
        self.set_interval(REFRESH_SECONDS, self.action_refresh)

    @work(thread=True)
    def bootstrap(self) -> None:
        try:
            board_cfg = self.client.get("/v1/config/board")
            me = self.client.get("/v1/whoami")
        except ClientError as err:
            self.call_from_thread(self.notify, err.message, title=err.code, severity="error",
                                  timeout=30)
            return
        try:
            self.scan_roots = self.client.get("/v1/config/scan")["workspaces"]
        except ClientError:
            self.scan_roots = []  # no Try starts from this device; everything else works
        self.columns = board_cfg["columns"]
        self.workspaces = board_cfg["workspaces"]
        self.tz = ZoneInfo(board_cfg["timezone"])
        self.device = me["device"]
        self.call_from_thread(self.switch_screen, HomeScreen())

    def load_selection_sync(self) -> None:
        """From a worker thread: this device's current selection, for markers/top bar."""
        try:
            found = self.client.get("/v1/selection", device=self.device)["selections"]
        except ClientError:
            return
        self.my_selection = found[0] if found else None

    def action_refresh(self) -> None:
        if isinstance(self.screen, PaneScreen):
            self.screen.reload()

    def action_help(self) -> None:
        if isinstance(self.screen, PaneScreen):
            self.screen.action_help()

    def action_cycle_theme(self) -> None:
        names = list(self.theme_specs)
        self.theme = names[(names.index(self.theme) + 1) % len(names)]
        try:
            theming.save_theme(self.theme, self.prefs_path)
        except OSError as err:
            self.notify(f"Theme not saved: {err}", severity="warning")
        self.notify(f"Theme: {self.theme}", timeout=2)
        if isinstance(self.screen, PaneScreen):
            self.screen.refresh_topbar()


class _Loading(Screen):
    def compose(self):
        yield Label("[$muted]Connecting to Mnemosyne…[/]")


def _attach_terminal() -> None:
    """Under `s`, stdout/stderr are pipes (for redaction); draw on the terminal instead.
    The token stays in this process's environment and is never displayed."""
    if not sys.stdout.isatty() and sys.stdin.isatty():
        fd = os.open("/dev/tty", os.O_RDWR)
        os.dup2(fd, 1)
        os.dup2(fd, 2)
        sys.stdout = sys.__stdout__ = os.fdopen(1, "w", buffering=1)
        sys.stderr = sys.__stderr__ = os.fdopen(2, "w", buffering=1)


def run(client: Client) -> None:
    _attach_terminal()
    MnemosyneApp(client).run()
