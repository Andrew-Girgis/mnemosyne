import json
from pathlib import Path

import httpx
import pytest

from mnemosyne import config as config_mod
from mnemosyne import devices
from mnemosyne.db import connect, migrate

CONFIG = """
timezone = "America/Toronto"
[devices.omphalos]
os = "linux"
home = "/home/girgie"
machine_names = ["omphalos"]
[devices.macbook]
os = "macos"
home = "/Users/agirgis"
machine_names = ["Andrews-Macbook-Air.local"]
[workspaces.personal]
base = "~/Projects"
try_root = "~/Projects/tries"
[workspaces.work]
base = "~/Quartermaster"
try_root = "~/Quartermaster/tries"
[[legacy_roots]]
device = "macbook"
path = "~/src/tries"
[wakapi]
base_url = "http://wakapi.test"
history_start = "2026-09-15"
"""


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("MNEMOSYNE_WAKAPI_API_KEY", "test-key")
    path = tmp_path / "mnemosyne.toml"
    path.write_text(CONFIG)
    return config_mod.load(path)


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "mnemosyne.sqlite3")


@pytest.fixture
def conn(config, db_path):
    c = connect(db_path)
    migrate(c)
    devices.sync(c, config)
    yield c
    c.close()


def heartbeat(t, entity, *, project="p", machine="omphalos", ua=None, hb_id=None):
    return {
        "id": str(hb_id if hb_id is not None else int(t)),
        "time": t,
        "entity": entity,
        "type": "file",
        "category": "coding",
        "project": project,
        "branch": "main",
        "language": "Python",
        "is_write": False,
        "machine_name_id": machine,
        "user_agent_id": ua or "wakatime/v1.90.0 (linux-6.0) go1.22 neovim/0.10 vim-wakatime/11",
    }


class FakeWakapi:
    """Serves /heartbeats?date= from an in-memory dict and records requests."""

    def __init__(self, days: dict[str, list[dict]]):
        self.days = days
        self.requests: list[str] = []

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(str(request.url))
            if not request.headers.get("authorization", "").startswith("Bearer "):
                return httpx.Response(401)
            day = request.url.params["date"]
            return httpx.Response(200, json={"data": self.days.get(day, [])})
        return httpx.MockTransport(handle)


def dump(obj) -> str:
    return json.dumps(obj, indent=2, default=str)


FIXTURES = Path(__file__).parent / "fixtures"
