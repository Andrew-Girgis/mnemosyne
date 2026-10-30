"""Cards, panes and formatting shared by the Home and Board screens."""

from datetime import datetime
from zoneinfo import ZoneInfo

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Label, ListItem, ListView, Static

TAGS = {"personal": "PERS", "work": "WORK", "legacy": "LEGACY"}


def fmt_duration(seconds: float | None) -> str:
    seconds = int(seconds or 0)
    if seconds < 60:
        return f"{seconds}s" if seconds else "0m"
    hours, minutes = divmod(seconds // 60, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


def fmt_date(ts: float | None, tz: ZoneInfo) -> str:
    return datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d") if ts else "—"


def fmt_time(ts: float | None, tz: ZoneInfo) -> str:
    return datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d %H:%M") if ts else "—"


def tag_for(workspace: str | None) -> str:
    return TAGS.get(workspace or "", (workspace or "?")[:4].upper())


def workspace_css(names: list[str]) -> str:
    """Card colours per workspace, from the theme's $ws-<name> variables."""
    rules = []
    for ws in [*names, "fallback"]:
        rules.append(f"""
        .card.ws-{ws} {{ border: round $ws-{ws}-dim; }}
        .card.ws-{ws} .card-dot, .card.ws-{ws} .card-tag {{ color: $ws-{ws}; }}
        ListView > .card.ws-{ws}.-highlight {{ border: round $ws-{ws}; }}
        ListView:focus > .card.ws-{ws}.-highlight {{ border: heavy $ws-{ws}; }}
        """)
    return "\n".join(rules)


def kv(label: str, value: str, width: int = 8) -> str:
    return f"[$muted]{label:<{width}}[/] {value}"


class Card(ListItem):
    """A bordered, selectable card. `workspace` picks its colour class."""

    def __init__(self, title: str, lines: list[str], workspace: str | None,
                 known: set[str], *, dot: str = "●", tag: str | None = None,
                 extra_classes: str = ""):
        ws = workspace if workspace in known else "fallback"
        super().__init__(classes=f"card ws-{ws} {extra_classes}".strip())
        self._title, self._lines, self._dot = title, lines, dot
        self._tag = tag if tag is not None else tag_for(workspace)

    def compose(self) -> ComposeResult:
        with Horizontal(classes="card-head"):
            yield Label(self._dot, classes="card-dot")
            yield Label(escape(self._title), classes="card-title", markup=True)
            if self._tag:
                yield Label(self._tag, classes="card-tag")
        for line in self._lines:
            yield Label(line, classes="card-line")


class IdeaCard(Card):
    def __init__(self, idea: dict, known: set[str]):
        lines = [f"[$muted]{escape(idea['summary'])}[/]"] if idea.get("summary") else []
        super().__init__(idea["title"], lines, idea["workspace"], known, dot="◆")
        self.idea = idea


class ProjectCard(Card):
    def __init__(self, project: dict, known: set[str], tz: ZoneInfo, selected: bool):
        lines = [kv("Started", fmt_date(project.get("started_at"), tz)),
                 kv("Time", fmt_duration(project.get("any_seconds")))]
        classes = "selected" if selected else ""
        super().__init__(project["name"], lines, project["workspace"], known,
                         dot="◉" if selected else "●", extra_classes=classes)
        self.project = project


class TaskCard(Card):
    def __init__(self, task: dict, workspace: str | None, known: set[str], tz: ZoneInfo,
                 selected: bool):
        role = task["column_name"].lower()
        if role == "done":
            meta, dot = kv("Done", fmt_date(task.get("done_at") or task["updated_at"], tz)), "✓"
        elif role == "paused":
            meta, dot = f"[$warning]PAUSED[/] [$muted]since[/] " \
                        f"{fmt_date(task.get('paused_at') or task['updated_at'], tz)}", "‖"
        elif role == "active":
            meta, dot = kv("Started", fmt_date(task.get("started_at") or task["created_at"],
                                               tz)), "▶"
        else:
            meta, dot = kv("Created", fmt_date(task["created_at"], tz)), "○"
        if selected:
            dot = "◉"
        classes = " ".join(c for c in (role, "selected" if selected else "") if c)
        super().__init__(task["title"], [meta], workspace, known, dot=dot, tag="",
                         extra_classes=classes)
        self.task_data = task


class AddItem(ListItem):
    """A visible '+ Add …' control at the top of a pane."""

    def __init__(self, label: str):
        super().__init__(Label(label), classes="add-item")


class Divider(ListItem):
    def __init__(self, label: str):
        super().__init__(Label(label), classes="divider", disabled=True)


class EmptyNote(ListItem):
    def __init__(self, label: str):
        super().__init__(Label(label), classes="empty-note", disabled=True)


class Pane(Vertical):
    """A titled, bordered pane holding one list."""

    def __init__(self, title: str, key: str, list_id: str, **kwargs):
        super().__init__(classes="pane", **kwargs)
        self.border_title = title
        self.border_subtitle = key
        self.list_id = list_id
        self.base_title = title

    def compose(self) -> ComposeResult:
        yield ListView(id=self.list_id)

    def set_count(self, count: int | str) -> None:
        self.border_title = f"{self.base_title} [$muted]{count}[/]"


class TopBar(Horizontal):
    def compose(self) -> ComposeResult:
        yield Label("", id="topbar-left")
        yield Label("", id="topbar-right")

    def set(self, left: str, right: str) -> None:
        self.query_one("#topbar-left", Label).update(left)
        self.query_one("#topbar-right", Label).update(right)


class DetailsBody(Static):
    pass
