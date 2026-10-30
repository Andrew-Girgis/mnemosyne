"""`mnemosyne` — the CLI shared by the user and agents. JSON on stdout, errors on stderr.

Exit codes: 0 ok · 2 usage · 3 auth/permission · 4 not found · 5 server unreachable ·
6 validation/conflict/upstream error · 1 anything else.
"""

import json
import sys
from typing import Annotated

import typer

from mnemosyne import client as client_mod
from mnemosyne import scanner, trystart

app = typer.Typer(no_args_is_help=True, help="Mnemosyne: projects, tasks and activity.")
projects_app = typer.Typer(no_args_is_help=True, help="Projects and their lifecycle.")
tasks_app = typer.Typer(no_args_is_help=True, help="Tasks on the board.")
ideas_app = typer.Typer(no_args_is_help=True, help="Ideas that may become projects.")
coding_app = typer.Typer(no_args_is_help=True, help="Wakapi coding activity.")
import_app = typer.Typer(no_args_is_help=True, help="Trigger imports.")
app.add_typer(projects_app, name="projects")
app.add_typer(tasks_app, name="tasks")
app.add_typer(ideas_app, name="ideas")
app.add_typer(coding_app, name="coding")
app.add_typer(import_app, name="import")

_state: dict = {"actor": None, "url": None}


@app.callback()
def _global(
    actor: Annotated[str | None, typer.Option(
        help="Override caller detection: 'user' or 'agent'.")] = None,
    url: Annotated[str | None, typer.Option(
        help="API base URL (else MNEMOSYNE_URL, else client.toml).")] = None,
):
    if actor not in (None, "user", "agent"):
        raise typer.BadParameter("--actor must be 'user' or 'agent'")
    _state.update(actor=actor, url=url)


def _fail(err: client_mod.ClientError):
    sys.stderr.write(json.dumps({"error": {"code": err.code, "message": err.message}}) + "\n")
    raise typer.Exit(err.exit_code)


def _client() -> client_mod.Client:
    identity = client_mod.detect(_state["actor"])
    try:
        return client_mod.Client(identity, client_mod.token_for(identity), _state["url"])
    except client_mod.ClientError as err:
        _fail(err)


def _out(fn):
    try:
        data = fn(_client())
    except client_mod.ClientError as err:
        _fail(err)
    typer.echo(json.dumps(data, indent=2, default=str))


# --- status ---------------------------------------------------------------------------


@app.command()
def health():
    """Server and importer status."""
    _out(lambda c: c.get("/health"))


@app.command()
def whoami():
    """Who the server thinks is calling (token kind, device, agent identity)."""
    def run(c):
        return {**c.get("/v1/whoami"), "detected": c.identity.__dict__}
    _out(run)


@app.command()
def scan(dry_run: Annotated[bool, typer.Option(help="print what would be sent")] = False):
    """Discover projects and commits on this device and report them to the server."""
    def run(c):
        roots = c.get("/v1/config/scan")
        entries, links, repos, complete = scanner.scan(roots["workspaces"])
        body = {"complete": complete, "entries": entries, "links": links, "repos": repos}
        if dry_run:
            return {"device": roots["device"], **body}
        return c.post("/v1/scans", body)
    _out(run)


# --- projects -------------------------------------------------------------------------


@projects_app.command("list")
def projects_list(workspace: str | None = None, lifecycle: str | None = None):
    _out(lambda c: c.get("/v1/projects", workspace=workspace, lifecycle=lifecycle))


@projects_app.command("show")
def projects_show(project: Annotated[str, typer.Argument(help="project id or key")]):
    _out(lambda c: c.get(f"/v1/projects/{project}"))


@projects_app.command("board")
def projects_board(project: str, archived: bool = False):
    """Tasks by column, coding totals and recent commits for one project."""
    _out(lambda c: c.get(f"/v1/projects/{project}/board", include_archived=archived))


@projects_app.command("create")
def projects_create(name: str, workspace: Annotated[str, typer.Option(...)],
                    lifecycle: str = "active"):
    """A project without a folder (agents may create projects too)."""
    _out(lambda c: c.post("/v1/projects",
                          {"name": name, "workspace": workspace, "lifecycle": lifecycle}))


@projects_app.command("lifecycle")
def projects_lifecycle(project: str,
                       state: Annotated[str, typer.Argument(help="active|paused|archived")]):
    """Move a project through its lifecycle (user only)."""
    _out(lambda c: c.patch(f"/v1/projects/{project}", {"lifecycle": state}))


@projects_app.command("describe")
def projects_describe(project: str, description: str):
    """Set a project's description (empty string clears it)."""
    _out(lambda c: c.patch(f"/v1/projects/{project}", {"description": description}))


@projects_app.command("merge")
def projects_merge(source: Annotated[str, typer.Argument(help="project to fold in")],
                   into: Annotated[str, typer.Option("--into", help="project that survives")]):
    """Merge a duplicate project into another (user only); the source key keeps resolving."""
    _out(lambda c: c.post(f"/v1/projects/{source}/merge", {"into": into}))


@projects_app.command("suggestions")
def projects_suggestions():
    """Possible moves the scanner could not decide on (user only)."""
    _out(lambda c: c.get("/v1/suggestions"))


@projects_app.command("resolve")
def projects_resolve(suggestion: int, accept: Annotated[bool, typer.Option(
        "--accept/--reject", help="accept keeps the old project's history under the new key")]):
    _out(lambda c: c.post(f"/v1/suggestions/{suggestion}", {"accept": accept}))


# --- tasks ----------------------------------------------------------------------------


@tasks_app.command("list")
def tasks_list(project: str | None = None, column: str | None = None, archived: bool = False):
    _out(lambda c: c.get("/v1/tasks", project=project, column=column,
                         include_archived=archived or None))


@tasks_app.command("create")
def tasks_create(title: str, project: Annotated[str, typer.Option(help="project id or key")],
                 description: str = "", column: str | None = None):
    _out(lambda c: c.post("/v1/tasks", {"project": project, "title": title,
                                        "description": description, "column": column}))


@tasks_app.command("show")
def tasks_show(task: str):
    """Task with notes, full edit history and linked agent sessions."""
    _out(lambda c: c.get(f"/v1/tasks/{task}"))


@tasks_app.command("edit")
def tasks_edit(task: str, title: str | None = None, description: str | None = None):
    _out(lambda c: c.patch(f"/v1/tasks/{task}", {"title": title, "description": description}))


@tasks_app.command("move")
def tasks_move(task: str, column: str):
    _out(lambda c: c.post(f"/v1/tasks/{task}/move", {"column": column}))


@tasks_app.command("note")
def tasks_note(task: str, body: str):
    _out(lambda c: c.post(f"/v1/tasks/{task}/notes", {"body": body}))


@tasks_app.command("archive")
def tasks_archive(task: str):
    """Soft-delete (user only); `unarchive` restores."""
    _out(lambda c: c.post(f"/v1/tasks/{task}/archive"))


@tasks_app.command("unarchive")
def tasks_unarchive(task: str):
    _out(lambda c: c.post(f"/v1/tasks/{task}/unarchive"))


# --- ideas ----------------------------------------------------------------------------


@ideas_app.command("list")
def ideas_list(status: Annotated[str, typer.Option(help="open|started|dropped|all")] = "open",
               workspace: str | None = None):
    _out(lambda c: c.get("/v1/ideas", status=status, workspace=workspace))


@ideas_app.command("create")
def ideas_create(title: str, workspace: Annotated[str, typer.Option(...)],
                 summary: str = "", body: str = ""):
    _out(lambda c: c.post("/v1/ideas", {"title": title, "workspace": workspace,
                                        "summary": summary, "body": body}))


@ideas_app.command("show")
def ideas_show(idea: str):
    """Idea with its project link and edit history."""
    _out(lambda c: c.get(f"/v1/ideas/{idea}"))


@ideas_app.command("edit")
def ideas_edit(idea: str, title: str | None = None, summary: str | None = None,
               body: str | None = None, workspace: str | None = None):
    _out(lambda c: c.patch(f"/v1/ideas/{idea}", {"title": title, "summary": summary,
                                                  "body": body, "workspace": workspace}))


@ideas_app.command("drop")
def ideas_drop(idea: str):
    """Soft-drop (user only); `reopen` restores."""
    _out(lambda c: c.post(f"/v1/ideas/{idea}/drop"))


@ideas_app.command("reopen")
def ideas_reopen(idea: str):
    _out(lambda c: c.post(f"/v1/ideas/{idea}/reopen"))


@ideas_app.command("start")
def ideas_start(
    idea: str,
    project: Annotated[str | None, typer.Option(
        help="link to this existing project instead of creating a Try folder")] = None,
    name: Annotated[str | None, typer.Option(
        help="Try folder name (default: from the title); `try` adds the date")] = None,
    workspace: Annotated[str | None, typer.Option(
        help="workspace for the Try folder (default: the idea's)")] = None,
):
    """Start a project from an idea: create a Try folder on this device with `try`, register
    it, and link the idea to it. With --project, only link to an existing project."""
    def run(c):
        if project:
            return c.post(f"/v1/ideas/{idea}/start", {"project": project})
        found = c.get(f"/v1/ideas/{idea}")["idea"]
        return trystart.start_idea(c, idea, workspace or found["workspace"],
                                   name or trystart.slugify(found["title"]))
    _out(run)


# --- selection ------------------------------------------------------------------------


@app.command()
def select(task: Annotated[str | None, typer.Argument(help="task id")] = None,
           project: Annotated[str | None, typer.Option(help="select a project, no task")] = None):
    """User: set this device's selection. Agent: link this agent session to the task
    (never changes the device selection)."""
    def run(c):
        if c.identity.kind == "agent":
            if not c.identity.session:
                raise client_mod.ClientError(
                    6, "no_session", "no agent session id detected; set MNEMOSYNE_ACTOR_SESSION")
            link = c.put(f"/v1/sessions/{c.identity.harness}/{c.identity.session}/task",
                         {"task": task})
            return {"scope": "session", **link}
        return {"scope": "device",
                **c.put("/v1/selection", {"task": task, "project": project})}
    _out(run)


@app.command()
def clear():
    """Clear this device's selection (user), or unlink this agent session."""
    def run(c):
        if c.identity.kind == "agent":
            link = c.put(f"/v1/sessions/{c.identity.harness}/{c.identity.session}/task",
                         {"task": None})
            return {"scope": "session", **link}
        return {"scope": "device", **c.delete("/v1/selection")}
    _out(run)


@app.command()
def current(device: str | None = None):
    """Current selection per device."""
    _out(lambda c: c.get("/v1/selection", device=device))


# --- coding + imports -------------------------------------------------------------------


@coding_app.command("summary")
def coding_summary(day: Annotated[str | None, typer.Option(help="YYYY-MM-DD")] = None,
                   days: Annotated[int, typer.Option(help="window ending on day")] = 1):
    _out(lambda c: c.get("/v1/coding/summary", day=day, days=days))


@import_app.command("wakapi")
def import_wakapi(from_start: bool = False):
    _out(lambda c: c.post("/v1/imports/wakapi", from_start=str(from_start).lower()))


# --- TUI ------------------------------------------------------------------------------


@app.command()
def tui():
    """Open the Kanban board (always acts as the user)."""
    from mnemosyne.tui.app import run

    identity = client_mod.Identity("user", source="tui")
    try:
        c = client_mod.Client(identity, client_mod.token_for(identity), _state["url"])
    except client_mod.ClientError as err:
        _fail(err)
    run(c)


def main() -> None:
    app()
