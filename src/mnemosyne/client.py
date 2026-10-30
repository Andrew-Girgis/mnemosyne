"""Shared API client for the CLI and TUI: who is calling, which token, HTTP calls.

Tokens come from `s` (global store, so any directory works): `MNEMOSYNE_TOKEN` for the
user, `MNEMOSYNE_AGENT_TOKEN` for agents. When the needed token is not in the environment,
the process re-executes itself under `s <VAR> -- …`, so the token only ever lives in env.
The server URL comes from `--url`, `MNEMOSYNE_URL`, or `url` in `client.toml` (see CONFIG_FILE).
"""

import os
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import httpx

CONFIG_FILE = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / \
    "mnemosyne" / "client.toml"
REEXEC_FLAG = "MNEMOSYNE_S_INJECTED"
TOKEN_VARS = {"user": "MNEMOSYNE_TOKEN", "agent": "MNEMOSYNE_AGENT_TOKEN"}

# AI_AGENT is the cross-harness convention (Claude Code, Pi, Hermes set it); the specific
# markers are fallbacks; Codex and OpenCode set neither, so their process is looked for.
_AI_AGENT_PREFIXES = {"claude-code": "claude-code", "pi": "pi", "hermes-agent": "hermes",
                      "codex": "codex", "opencode": "opencode"}
_MARKERS = (("CLAUDECODE", "claude-code"), ("PI_CODING_AGENT", "pi"),
            ("HERMES_AGENT", "hermes"))
_SESSION_VARS = {"claude-code": "CLAUDE_CODE_SESSION_ID"}
# Only for harnesses without an env marker; short names like 'pi' would be too ambiguous.
_PARENT_NAMES = {"codex": "codex", "opencode": "opencode"}


@dataclass(frozen=True)
class Identity:
    kind: str  # 'user' | 'agent'
    harness: str | None = None
    session: str | None = None
    source: str = "default"  # how it was decided, for `whoami`


class ClientError(Exception):
    def __init__(self, exit_code: int, code: str, message: str):
        super().__init__(message)
        self.exit_code, self.code, self.message = exit_code, code, message


def _harness_from_ai_agent(value: str) -> str:
    lowered = value.lower()
    for prefix, harness in _AI_AGENT_PREFIXES.items():
        if lowered.startswith(prefix):
            return harness
    return lowered.split("_", 1)[0]


def _parent_commands(max_depth: int = 25) -> list[str]:
    """Process names up the parent chain (Linux /proc, else `ps`)."""
    names, pid = [], os.getppid()
    for _ in range(max_depth):
        if pid <= 1:
            break
        proc = Path(f"/proc/{pid}")
        try:
            if proc.exists():
                names.append((proc / "comm").read_text().strip())
                pid = int((proc / "stat").read_text().rsplit(")", 1)[1].split()[1])
                continue
            out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)],
                                 capture_output=True, text=True, timeout=2, check=False)
            ppid, _, comm = out.stdout.strip().partition(" ")
            names.append(Path(comm.strip()).name)
            pid = int(ppid)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            break
    return names


def detect(explicit: str | None = None, env: dict | None = None) -> Identity:
    env = os.environ if env is None else env
    session_override = env.get("MNEMOSYNE_ACTOR_SESSION")
    choice = explicit or env.get("MNEMOSYNE_ACTOR")
    if choice == "user":
        return Identity("user", source="explicit")
    harness, source = env.get("MNEMOSYNE_ACTOR_HARNESS"), "explicit"
    if not harness and env.get("AI_AGENT"):
        harness, source = _harness_from_ai_agent(env["AI_AGENT"]), "AI_AGENT"
    if not harness:
        for var, name in _MARKERS:
            if env.get(var):
                harness, source = name, var
                break
    if not harness and env is os.environ:
        for command in _parent_commands():
            if command in _PARENT_NAMES:
                harness, source = _PARENT_NAMES[command], f"parent:{command}"
                break
    if not harness:
        if choice == "agent":
            return Identity("agent", "unknown", session_override, "explicit")
        return Identity("user")
    session = session_override or env.get(_SESSION_VARS.get(harness, ""), None)
    return Identity("agent", harness, session, source)


def token_for(identity: Identity) -> str:
    """Return the token, re-executing under `s` once if it is not in the environment."""
    var = TOKEN_VARS[identity.kind]
    token = os.environ.get(var)
    if token:
        return token
    if not os.environ.get(REEXEC_FLAG) and shutil.which("s"):
        env = {**os.environ, REEXEC_FLAG: "1"}
        os.execvpe("s", ["s", var, "--", sys.executable, "-m", "mnemosyne", *sys.argv[1:]], env)
    raise ClientError(3, "no_token",
                      f"{var} is not available (store it with `s set {var}` in the global store)")


def server_url(explicit: str | None = None, config_file: Path | None = None) -> str:
    """The API base URL: `--url`, then MNEMOSYNE_URL, then `url` in the client config file."""
    if explicit or os.environ.get("MNEMOSYNE_URL"):
        return explicit or os.environ["MNEMOSYNE_URL"]
    path = config_file or CONFIG_FILE
    try:
        url = tomllib.loads(path.read_text()).get("url")
    except FileNotFoundError:
        url = None
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ClientError(2, "bad_config", f"cannot read {path}: {exc}") from exc
    if not url:
        raise ClientError(2, "no_url", "no server URL: pass --url, set MNEMOSYNE_URL, or put "
                          f'url = "https://…" in {path}')
    return url


EXIT_CODES = {401: 3, 403: 3, 404: 4, 409: 6, 422: 6, 502: 6}


class Client:
    def __init__(self, identity: Identity, token: str, url: str | None = None,
                 transport: httpx.BaseTransport | None = None):
        headers = {"Authorization": f"Bearer {token}"}
        if identity.kind == "agent":
            headers["X-Mnemosyne-Harness"] = identity.harness or "unknown"
            if identity.session:
                headers["X-Mnemosyne-Session"] = identity.session
        self.identity = identity
        self._http = httpx.Client(base_url=server_url(url),
                                  headers=headers, timeout=120, transport=transport)

    def request(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            raise ClientError(5, "unreachable", f"cannot reach Mnemosyne: {exc}") from exc
        if response.status_code >= 400:
            try:
                err = response.json()["error"]
            except (ValueError, KeyError, TypeError):
                err = {"code": "http_error", "message": response.text[:500]}
            raise ClientError(EXIT_CODES.get(response.status_code, 1), err["code"],
                              err["message"])
        return response.json()

    def get(self, path: str, **params) -> dict:
        return self.request("GET", path, params={k: v for k, v in params.items()
                                                  if v is not None})

    def post(self, path: str, body: dict | None = None, **params) -> dict:
        return self.request("POST", path, json=body or {}, params=params or None)

    def put(self, path: str, body: dict) -> dict:
        return self.request("PUT", path, json=body)

    def patch(self, path: str, body: dict) -> dict:
        return self.request("PATCH", path, json=body)

    def delete(self, path: str, **params) -> dict:
        return self.request("DELETE", path, params=params or None)
