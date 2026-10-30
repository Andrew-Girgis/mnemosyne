"""Server configuration (TOML). One file on the server describes every device and root."""

import os
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class Device:
    id: str
    os: str
    home: str
    machine_names: tuple[str, ...]

    def expand(self, path: str) -> str:
        """Map a '~/…' config path to this device's absolute path."""
        if path == "~" or path.startswith("~/"):
            return self.home.rstrip("/") + path[1:]
        return path


@dataclass(frozen=True)
class Workspace:
    name: str
    base: str  # '~/Projects' — graduated projects live directly here
    try_root: str  # '~/Projects/tries'
    mirror_root: str | None = None  # bare Git mirrors (try-workspaces), where a device has them


@dataclass(frozen=True)
class LegacyRoot:
    device: str
    path: str


@dataclass(frozen=True)
class WakapiConfig:
    base_url: str = "http://wakapi:3000"
    poll_seconds: int = 300
    history_start: date = date(2013, 1, 1)  # first day the initial/--from-start import reads
    api_key: str = ""


@dataclass(frozen=True)
class Config:
    timezone: str
    devices: dict[str, Device]
    workspaces: dict[str, Workspace]
    legacy_roots: tuple[LegacyRoot, ...] = ()
    wakapi: WakapiConfig = field(default_factory=WakapiConfig)
    seed_active_days: int = 14
    task_columns: tuple[str, ...] = ("Inbox", "Active", "Paused", "Done")

    def device_for_machine(self, machine_name: str | None) -> Device | None:
        if not machine_name:
            return None
        lowered = machine_name.lower()
        for device in self.devices.values():
            if lowered in (m.lower() for m in device.machine_names):
                return device
        return None


def load(path: str | Path) -> Config:
    raw = tomllib.loads(Path(path).read_text())
    devices = {
        name: Device(name, d["os"], d["home"], tuple(d.get("machine_names", [])))
        for name, d in raw.get("devices", {}).items()
    }
    workspaces = {
        name: Workspace(name, w["base"], w["try_root"], w.get("mirror_root"))
        for name, w in raw.get("workspaces", {}).items()
    }
    legacy = tuple(LegacyRoot(r["device"], r["path"]) for r in raw.get("legacy_roots", []))
    wk = raw.get("wakapi", {})
    wakapi = WakapiConfig(
        base_url=wk.get("base_url", WakapiConfig.base_url),
        poll_seconds=int(wk.get("poll_seconds", WakapiConfig.poll_seconds)),
        history_start=date.fromisoformat(
            str(wk.get("history_start", WakapiConfig.history_start.isoformat()))
        ),
        # The key is injected by the environment (s → compose), never written to the file.
        api_key=os.environ.get("MNEMOSYNE_WAKAPI_API_KEY", ""),
    )
    for root in legacy:
        if root.device not in devices:
            raise ValueError(f"legacy root refers to unknown device {root.device!r}")
    return Config(
        timezone=raw.get("timezone", "America/Toronto"),
        devices=devices,
        workspaces=workspaces,
        legacy_roots=legacy,
        wakapi=wakapi,
        seed_active_days=int(raw.get("seed_active_days", 14)),
        task_columns=tuple(raw.get("task_columns", Config.task_columns)),
    )
