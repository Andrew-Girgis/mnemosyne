"""Named TUI themes from TOML files, and the per-device preferences file.

Bundled themes live next to this module (`themes/*.toml`). Any `*.toml` in
`~/.config/mnemosyne/themes/` adds a theme, or replaces a bundled one with the same name.
A theme file has `name`, `dark`, a `[colors]` table of semantic tokens and a `[workspaces]`
table of card colours (one per workspace, plus `fallback`). The chosen theme name is kept in
`~/.config/mnemosyne/tui.toml`.
"""

import os
import re
import tomllib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from textual.color import Color, ColorParseError
from textual.theme import Theme

DEFAULT_THEME = "dark"
COLOR_TOKENS = ("background", "surface", "panel", "foreground", "muted", "faint", "accent",
                "selection", "warning", "error", "success")
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class ThemeError(ValueError):
    pass


@dataclass(frozen=True)
class ThemeSpec:
    name: str
    dark: bool
    colors: dict[str, str]
    workspaces: dict[str, str]  # always has 'fallback'
    source: str

    def workspace_color(self, workspace: str | None) -> str:
        return self.workspaces.get(workspace or "", self.workspaces["fallback"])


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "mnemosyne"


def _color(value, where: str) -> str:
    try:
        return Color.parse(str(value)).hex
    except ColorParseError as exc:
        raise ThemeError(f"{where}: {value!r} is not a colour") from exc


def parse(text: str, source: str) -> ThemeSpec:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ThemeError(f"{source}: {exc}") from exc
    name = raw.get("name")
    if not isinstance(name, str) or not _NAME.match(name):
        raise ThemeError(f"{source}: `name` must be lowercase letters, digits, - or _")
    colors = raw.get("colors", {})
    missing = [t for t in COLOR_TOKENS if t not in colors]
    if missing:
        raise ThemeError(f"{source}: [colors] is missing {', '.join(missing)}")
    workspaces = raw.get("workspaces", {})
    if "fallback" not in workspaces:
        raise ThemeError(f"{source}: [workspaces] needs a `fallback` colour")
    for ws in workspaces:
        if not _NAME.match(ws):
            raise ThemeError(f"{source}: workspace name {ws!r} is not a valid identifier")
    return ThemeSpec(
        name=name,
        dark=bool(raw.get("dark", True)),
        colors={t: _color(colors[t], f"{source} colors.{t}") for t in COLOR_TOKENS},
        workspaces={k: _color(v, f"{source} workspaces.{k}") for k, v in workspaces.items()},
        source=source,
    )


def load_themes(user_dir: Path | None = None) -> tuple[dict[str, ThemeSpec], list[str]]:
    """Bundled themes, then user themes (same name replaces). Returns (themes, problems):
    a broken user file is reported, never fatal."""
    themes: dict[str, ThemeSpec] = {}
    problems: list[str] = []
    for entry in sorted(resources.files("mnemosyne.tui.themes").iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".toml"):
            spec = parse(entry.read_text(), f"bundled {entry.name}")
            themes[spec.name] = spec
    user_dir = config_dir() / "themes" if user_dir is None else user_dir
    if user_dir.is_dir():
        for path in sorted(user_dir.glob("*.toml")):
            try:
                spec = parse(path.read_text(), str(path))
            except (ThemeError, OSError) as exc:
                problems.append(str(exc))
                continue
            themes[spec.name] = spec
    return themes, problems


def workspace_names(themes: dict[str, ThemeSpec], extra: list[str] = ()) -> list[str]:
    names = {ws for spec in themes.values() for ws in spec.workspaces} | set(extra)
    names.discard("fallback")
    return sorted(n for n in names if _NAME.match(n))


def to_textual(spec: ThemeSpec, workspaces: list[str]) -> Theme:
    """Our semantic tokens become Textual theme colours plus CSS variables:
    $muted $faint $selection $ws-<name> $ws-fallback (and the -dim variants for borders)."""
    c = spec.colors
    variables = {
        "muted": c["muted"],
        "faint": c["faint"],
        "selection": c["selection"],
        "footer-key-foreground": c["accent"],
        "footer-description-foreground": c["muted"],
        "footer-background": c["surface"],
        "footer-item-background": c["surface"],
        "input-selection-background": f"{c['accent']} 35%",
        "input-cursor-background": c["accent"],
        "input-cursor-foreground": c["background"],
        # List cursors: a tinted row, never inverted text (cards carry their own borders).
        "block-cursor-background": c["selection"],
        "block-cursor-foreground": c["foreground"],
        "block-cursor-text-style": "bold",
        "block-cursor-blurred-background": c["panel"],
        "block-cursor-blurred-foreground": c["foreground"],
        "block-cursor-blurred-text-style": "none",
        "block-hover-background": c["panel"],
        "scrollbar": c["faint"],
        "scrollbar-hover": c["muted"],
        "scrollbar-active": c["accent"],
        "scrollbar-background": c["surface"],
        "scrollbar-corner-color": c["surface"],
    }
    for ws in [*workspaces, "fallback"]:
        color = spec.workspace_color(ws)
        variables[f"ws-{ws}"] = color
        # Idle card borders: the workspace colour blended halfway into the card background.
        variables[f"ws-{ws}-dim"] = Color.parse(c["panel"]).blend(Color.parse(color), 0.45).hex
    return Theme(
        name=spec.name, dark=spec.dark, primary=c["accent"], secondary=c["muted"],
        accent=c["accent"], foreground=c["foreground"], background=c["background"],
        surface=c["surface"], panel=c["panel"], boost=c["selection"], warning=c["warning"],
        error=c["error"], success=c["success"], variables=variables,
    )


# --- preferences ------------------------------------------------------------------------


def prefs_path() -> Path:
    return config_dir() / "tui.toml"


def load_prefs(path: Path | None = None) -> dict:
    path = prefs_path() if path is None else path
    try:
        return tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def save_theme(name: str, path: Path | None = None) -> None:
    """Persist the theme choice, keeping any other keys in the file."""
    path = prefs_path() if path is None else path
    prefs = {**load_prefs(path), "theme": name}
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Mnemosyne TUI preferences (this device). Edited by the app; `T` cycles themes."]
    for key, value in prefs.items():
        if isinstance(value, str):
            lines.append(f"{key} = {_toml_str(value)}")
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    tmp.replace(path)


def _toml_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
