"""Dialogs: forms, confirmations, idea actions, starting a project, help, details overlay."""

import os

from rich.markup import escape
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, Label, ListItem, ListView, Select, Static, TextArea

from mnemosyne.client import Client, ClientError


class Dialog(ModalScreen):
    """Base: a centred bordered box; esc cancels."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def action_cancel(self) -> None:
        self.dismiss(None)


class TextPrompt(Dialog):
    def __init__(self, title: str, value: str = "", placeholder: str = ""):
        super().__init__()
        self.prompt_title, self.value, self.placeholder = title, value, placeholder

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.prompt_title, classes="dialog-title")
            yield Input(value=self.value, placeholder=self.placeholder, id="prompt-input")
            yield Label("enter save · esc cancel", classes="hint")

    @on(Input.Submitted)
    def submit(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)


class TextForm(Dialog):
    """One multi-line field (project description)."""

    BINDINGS = [Binding("ctrl+s", "save", "Save"), Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, value: str = ""):
        super().__init__()
        self.form_title, self.value = title, value

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.form_title, classes="dialog-title")
            yield TextArea(self.value, id="form-text", classes="tall")
            yield Label("ctrl+s save · esc cancel", classes="hint")

    def action_save(self) -> None:
        self.dismiss({"text": self.query_one("#form-text", TextArea).text})


class TaskForm(Dialog):
    BINDINGS = [Binding("ctrl+s", "save", "Save"), Binding("escape", "cancel", "Cancel")]

    def __init__(self, heading: str, title: str = "", description: str = ""):
        super().__init__()
        self.heading, self.title_value, self.description = heading, title, description

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.heading, classes="dialog-title")
            yield Label("Title", classes="field-label")
            yield Input(value=self.title_value, placeholder="What needs doing", id="task-title")
            yield Label("Description", classes="field-label")
            yield TextArea(self.description, id="task-description")
            yield Label("enter → description · ctrl+s save · esc cancel", classes="hint")

    def action_save(self) -> None:
        title = self.query_one("#task-title", Input).value.strip()
        if not title:
            self.notify("A title is required", severity="warning")
            return
        self.dismiss({"title": title,
                      "description": self.query_one("#task-description", TextArea).text})

    @on(Input.Submitted, "#task-title")
    def title_enter(self) -> None:
        self.query_one("#task-description", TextArea).focus()


class IdeaForm(Dialog):
    BINDINGS = [Binding("ctrl+s", "save", "Save"), Binding("escape", "cancel", "Cancel")]

    def __init__(self, heading: str, workspaces: list[str], idea: dict | None = None,
                 default_workspace: str | None = None):
        super().__init__()
        self.heading, self.workspaces, self.idea = heading, workspaces, idea or {}
        self.default_workspace = (self.idea.get("workspace") or default_workspace
                                  or workspaces[0])

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.heading, classes="dialog-title")
            yield Label("Title", classes="field-label")
            yield Input(value=self.idea.get("title", ""), placeholder="Name the idea",
                        id="idea-title")
            yield Label("Summary (one line, shown on the card)", classes="field-label")
            yield Input(value=self.idea.get("summary", ""), placeholder="What it is, briefly",
                        id="idea-summary")
            yield Label("Workspace", classes="field-label")
            yield Select([(w, w) for w in self.workspaces], value=self.default_workspace,
                         allow_blank=False, id="idea-workspace")
            yield Label("Notes", classes="field-label")
            yield TextArea(self.idea.get("body", ""), id="idea-body")
            yield Label("enter → next field · ctrl+s save · esc cancel", classes="hint")

    @on(Input.Submitted, "#idea-title")
    def to_summary(self) -> None:
        self.query_one("#idea-summary", Input).focus()

    @on(Input.Submitted, "#idea-summary")
    def to_workspace(self) -> None:
        self.query_one("#idea-workspace", Select).focus()

    def action_save(self) -> None:
        title = self.query_one("#idea-title", Input).value.strip()
        if not title:
            self.notify("A title is required", severity="warning")
            return
        self.dismiss({
            "title": title,
            "summary": self.query_one("#idea-summary", Input).value.strip(),
            "workspace": self.query_one("#idea-workspace", Select).value,
            "body": self.query_one("#idea-body", TextArea).text,
        })


class Confirm(Dialog):
    BINDINGS = [Binding("y", "yes", "Yes"), Binding("n,escape", "no", "No")]

    def __init__(self, question: str, detail: str = ""):
        super().__init__()
        self.question, self.detail = question, detail

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.question, classes="dialog-title")
            if self.detail:
                yield Label(self.detail, classes="dialog-text")
            yield Label("y yes · n no", classes="hint")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class IdeaActions(Dialog):
    """Opening an idea: edit it, start a project from it, or drop it."""

    BINDINGS = [Binding("e", "choose('edit')", "Edit"),
                Binding("t", "choose('start')", "Start project"),
                Binding("d", "choose('drop')", "Drop"),
                Binding("escape,q", "cancel", "Close")]

    def __init__(self, idea: dict):
        super().__init__()
        self.idea = idea

    def compose(self) -> ComposeResult:
        i = self.idea
        with Vertical(classes="dialog"):
            yield Label(escape(i["title"]), classes="dialog-title")
            if i.get("summary"):
                yield Label(escape(i["summary"]), classes="dialog-text")
            with Horizontal(classes="choices"):
                yield Label("[b $accent]e[/] edit", classes="choice")
                yield Label("[b $accent]t[/] start project with try", classes="choice")
                yield Label("[b $accent]d[/] drop", classes="choice")
            yield Label("esc close", classes="hint")

    def action_choose(self, choice: str) -> None:
        self.dismiss(choice)


class StartProject(Dialog):
    """Pick the workspace and folder name; `try` adds the date and de-duplicates."""

    def __init__(self, idea: dict, workspaces: list[str], try_roots: dict[str, str],
                 slug: str):
        super().__init__()
        self.idea, self.workspaces, self.try_roots, self.slug = idea, workspaces, try_roots, slug

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(f"Start a project from “{escape(self.idea['title'])}”",
                        classes="dialog-title")
            yield Label("Workspace", classes="field-label")
            yield Select([(w, w) for w in self.workspaces], value=self.idea["workspace"],
                         allow_blank=False, id="start-workspace")
            yield Label("Folder name", classes="field-label")
            yield Input(value=self.slug, id="start-name",
                        restrict=r"[a-z0-9._-]*")
            yield Static("", id="start-preview", classes="dialog-text")
            yield Label("enter create with try · esc cancel", classes="hint")

    def on_mount(self) -> None:
        self._preview()
        self.query_one("#start-name", Input).focus()

    @on(Input.Changed)
    @on(Select.Changed)
    def _preview(self) -> None:
        ws = self.query_one("#start-workspace", Select).value
        name = self.query_one("#start-name", Input).value or "…"
        root = self.try_roots.get(ws, "?")
        home = os.path.expanduser("~")
        if root.startswith(home + "/"):
            root = "~" + root[len(home):]
        self.query_one("#start-preview", Static).update(
            f"[$muted]try creates[/] {escape(root)}/[b]YYYY-MM-DD-{escape(name)}[/]\n"
            f"[$muted]on this device, then Mnemosyne links it to this idea[/]")

    @on(Input.Submitted, "#start-name")
    def submit(self) -> None:
        name = self.query_one("#start-name", Input).value.strip("-.")
        if not name:
            self.notify("A folder name is required", severity="warning")
            return
        self.dismiss({"workspace": self.query_one("#start-workspace", Select).value,
                      "name": name})


class Suggestions(Dialog):
    """Possible moves the scanner could not decide on."""

    BINDINGS = [Binding("a", "decide(True)", "Accept"), Binding("r", "decide(False)", "Reject"),
                Binding("escape,q", "cancel", "Close")]

    def __init__(self, client: Client):
        super().__init__()
        self.client, self.items = client, []

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide"):
            yield Label("Possible project moves", classes="dialog-title")
            yield ListView(id="suggestions")
            yield Label("a accept (keeps the old history under the new location) · r reject ·"
                        " esc close", classes="hint")

    def on_mount(self) -> None:
        self.load()

    @work(thread=True, exclusive=True)
    def load(self) -> None:
        try:
            items = self.client.get("/v1/suggestions")["suggestions"]
        except ClientError as err:
            self.app.call_from_thread(self.app.notify, err.message, severity="error")
            return
        self.app.call_from_thread(self._show, items)

    def _show(self, items: list[dict]) -> None:
        self.items = items
        view = self.query_one("#suggestions", ListView)
        view.clear()
        for s in items:
            view.append(ListItem(Label(f"{escape(s['from_key'])}  →  {escape(s['to_key'])}"
                                       f"   [$muted]({s['evidence']})[/]")))
        if not items:
            view.append(ListItem(Label("[$muted]Nothing to decide.[/]")))

    @work(thread=True)
    def action_decide(self, accept: bool) -> None:
        index = self.query_one("#suggestions", ListView).index
        if index is None or index >= len(self.items):
            return
        try:
            self.client.post(f"/v1/suggestions/{self.items[index]['id']}", {"accept": accept})
        except ClientError as err:
            self.app.call_from_thread(self.app.notify, err.message, severity="error")
        self.load()


class HelpScreen(Dialog):
    """Every key for the screen underneath, generated from its bindings."""

    BINDINGS = [Binding("escape,q,question_mark", "cancel", "Close")]

    def __init__(self, sections: list[tuple[str, list[tuple[str, str]]]]):
        super().__init__()
        self.sections = sections

    def compose(self) -> ComposeResult:
        lines = []
        for heading, keys in self.sections:
            lines.append(f"[b $accent]{heading}[/]")
            lines += [f"  [b]{escape(k):<14}[/] {escape(d)}" for k, d in keys]
            lines.append("")
        with Vertical(classes="dialog wide"):
            yield Label("Keys", classes="dialog-title")
            with VerticalScroll(classes="help-body"):
                yield Static("\n".join(lines))
            yield Label("esc close", classes="hint")


class DetailsOverlay(Dialog):
    """Details for narrow terminals, where the Details pane is hidden."""

    BINDINGS = [Binding("escape,q,v", "cancel", "Close")]

    def __init__(self, markup: str):
        super().__init__()
        self.markup = markup

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide"):
            with VerticalScroll(classes="help-body"):
                yield Static(self.markup)
            yield Label("esc close", classes="hint")
