"""Start a project from an idea with the `try` CLI, on this device.

`try` owns the naming: `try exec . <name>` (run from a directory that is not a Git repo)
prints a shell script whose first command is `mkdir -p '<try_root>/YYYY-MM-DD-<name>'`,
with its own de-duplication (`-2`, bumped numbers). Only that directory is created here;
the rest of the script (touch/echo/cd) is for interactive shells. The new folder is then
reported as a (partial) scan so the server registers the project, and the idea is linked.
`try-workspaces entry-guard` and `claude-project-move` are not run: a brand-new folder has
no active-device marker and no Claude history to move.
"""

import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

from mnemosyne import scanner
from mnemosyne.client import Client, ClientError

_MKDIR = re.compile(r"^\s*mkdir -p (.+?)(?:\s*&&.*)?$")
_UNSAFE = re.compile(r"[^a-z0-9._-]+")


def slugify(title: str) -> str:
    """'Garden sensor (v2)!' → 'garden-sensor-v2' — the style of existing Try names."""
    return _UNSAFE.sub("-", title.strip().lower()).strip("-.")[:60].rstrip("-.")


def planned_path(try_root: str, projects_root: str, name: str, try_bin: str = "try") -> Path:
    """Ask `try` where it would create a new try called `name`."""
    exe = shutil.which(try_bin)
    if exe is None:
        raise ClientError(6, "try_missing", "the `try` CLI is not installed on this device")
    if not slugify(name) or slugify(name) != name:
        raise ClientError(6, "invalid_name", f"folder name must look like {slugify(name)!r}")
    env = {**os.environ, "TRY_PATH": try_root, "TRY_PROJECTS": projects_root,
           "SHELL": "/bin/sh"}  # plain POSIX script output whatever the login shell is
    with tempfile.TemporaryDirectory() as not_a_repo:
        try:
            out = subprocess.run([exe, "exec", ".", name], cwd=not_a_repo, env=env,
                                 stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                 timeout=20, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ClientError(6, "try_failed", f"`try exec` did not run: {exc}") from exc
    if out.returncode != 0:
        raise ClientError(6, "try_failed",
                          f"`try exec` failed ({out.returncode}): {out.stderr.strip()[:300]}")
    for line in out.stdout.splitlines():
        match = _MKDIR.match(line)
        if match:
            words = shlex.split(match.group(1))
            if len(words) != 1:
                break
            path = Path(words[0])
            if path.parent.resolve() != Path(try_root).resolve():
                raise ClientError(6, "try_unexpected",
                                  f"`try` planned {path}, which is not directly in {try_root}")
            return path
    raise ClientError(6, "try_unexpected",
                      "could not find the `mkdir -p` line in `try exec` output:\n"
                      + out.stdout[:300])


def start_idea(client: Client, idea_id: str, workspace: str, name: str,
               try_bin: str = "try") -> dict:
    """Create the Try folder for an idea, register it, and link the idea to it.
    Returns the server's `/start` response plus the created `path`."""
    roots = client.get("/v1/config/scan")
    ws = next((w for w in roots["workspaces"] if w["name"] == workspace), None)
    if ws is None:
        raise ClientError(6, "invalid_workspace", f"unknown workspace {workspace!r}")
    idea = client.get(f"/v1/ideas/{idea_id}")["idea"]
    if idea["status"] != "open":
        raise ClientError(6, "not_open", f"idea is {idea['status']}, not open")
    try:
        Path(ws["try_root"]).mkdir(parents=True, exist_ok=True)
        path = planned_path(ws["try_root"], ws["base"], name, try_bin)
        path.mkdir(exist_ok=False)
    except OSError as exc:
        raise ClientError(6, "try_folder", f"could not create the Try folder: {exc}") from exc
    entry = scanner.entry_for(workspace, Path(ws["base"]), path, "try")
    # An empty folder has no file mtimes; it was created just now, so it starts Active.
    entry["newest_mtime"] = entry["newest_mtime"] or path.stat().st_mtime
    key = f"{workspace}/{entry['relpath']}"
    try:
        client.post("/v1/scans",
                    {"complete": False, "entries": [entry], "links": [], "repos": []})
        result = client.post(f"/v1/ideas/{idea_id}/start", {"project": key})
    except ClientError as err:
        raise ClientError(err.exit_code, err.code,
                          f"folder {path} was created, but registering it as {key} or linking"
                          f" the idea failed: {err.message}. Retry with `mnemosyne scan` then"
                          f" `mnemosyne ideas start {idea_id} --project {key}`.") from err
    return {**result, "path": str(path)}
