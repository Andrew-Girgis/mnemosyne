# TUI

`mnemosyne tui` opens a keyboard-driven terminal UI. It always acts as the user and uses the
same API as the CLI. `?` lists every key for the current screen.

## Screens

```
HOME   Ideas │ Projects │ Details
  enter on an idea     → edit it (e), start a project from it with try (t), or drop it (d)
  enter on a project   → BOARD   Inbox │ Active (+ paused) │ Done │ Details
                            esc / backspace → back to Home
```

- **Idea cards** show the title, one-line summary and a `PERS`/`WORK` tag. **Project cards**
  show the name, start date and all-time coding time (you + agents, unioned). Everything else
  is in Details.
- **Details for a project** shows its description, the idea it came from, coding time for
  today/week/all split into you/agents/either, active and paused tasks, and recent commits.
- **Details for a task** shows its status, created/started/paused/done dates (from the audit
  log), description, column timeline, notes, linked agent sessions and history. Tasks have no
  durations yet.
- **Paused tasks** live in the Active pane under a `── paused n ──` divider; the header reads
  `ACTIVE 3 · ‖ 2`. Their column is still `Paused`; nothing is remapped.
- **Narrow terminals:** below 110 columns Details is hidden and `v` opens it as an overlay;
  below 70 columns one pane shows at a time (`tab` / number keys switch).

## Keys

| Where | Key | Action |
| --- | --- | --- |
| everywhere | `?` `T` `r` `q` | help, next theme, refresh, quit |
| everywhere | `1`–`3`, `tab` / `shift+tab`, `↑↓` | focus a pane, move |
| Home | `enter` | open the idea or the project board |
| Home | `i` (or enter on **+ Add idea**) | new idea |
| Home | `e` | edit the idea / the project's description |
| Home | `t` | start a project from the highlighted idea with `try` |
| Home | `d` | drop the idea (`mnemosyne ideas reopen` restores it) |
| Home | `p`, `L` | new project without a folder, cycle the project's lifecycle |
| Home | `w`, `f` | filter by workspace, by lifecycle (default: active) |
| Home / Board | `s`, `x` | select the project/task for time attribution, clear selection |
| Home | `m` | possible project moves |
| Board | `n`, `e`, `a` | new task in the focused pane, edit, add a note |
| Board | `[` `]` | move left/right: Inbox ↔ Active ↔ Done (a paused task moves too) |
| Board | `z`, `D` | pause/resume an active task, mark done |
| Board | `d` | archive the task (`mnemosyne tasks unarchive` restores it) |
| Board | `esc`, `backspace` | back to Home |

## Starting a project from an idea

`t` asks for the workspace and a folder name (from the title, e.g. `garden-sensor`). Then, **on
the device running the TUI**:

1. `TRY_PATH=<workspace try root> try exec . <name>`, run from a directory that is not a Git
   repo, says where `try` would create the try: `<try root>/YYYY-MM-DD-<name>`, with `try`'s
   own de-duplication (`-2`, …). Only that `mkdir` is done, and only directly inside the try root.
2. The folder is reported to the server as a partial scan, so the project exists straight away
   (as Active).
3. The idea is marked started and linked to the project. An empty project description takes
   the idea's summary and notes. The idea leaves the Ideas pane; the project's Details shows
   `From idea`.
4. A `cd <path>` line is copied to the clipboard (the TUI cannot change your shell's
   directory). `try-workspaces entry-guard` and `claude-project-move` are not needed for a
   brand-new folder.

The CLI equivalent is `mnemosyne ideas start <idea>` (`--name`, `--workspace`), or
`--project <key>` to link an existing project instead.

## Themes

Bundled: `dark` (default) and `light` after Claude Code's themes, `phosphor` (green CRT) and
`amber`. `T` cycles them and saves the choice to `~/.config/mnemosyne/tui.toml` (per device).

A theme is a TOML file. Drop one into `~/.config/mnemosyne/themes/` to add a theme, or to
replace a bundled one with the same `name`. A broken file is reported and skipped.

```toml
name = "mine"
dark = true

[colors]
background = "#141413"
surface = "#1a1a19"      # panes and cards
panel = "#1f1e1d"        # dialogs
foreground = "#e8e6dc"
muted = "#999999"        # labels, secondary text
faint = "#3d3d3a"        # idle pane borders
accent = "#d77757"       # focused pane, titles, selected card title
selection = "#2e2621"    # list/editor selection tint
warning = "#ffc107"
error = "#ff6b80"
success = "#4eba65"

[workspaces]             # card colour per workspace; any other workspace uses fallback
personal = "#5fb3a6"
work = "#b1b9f9"
fallback = "#999999"
```

Cards take their border and tag colour from their workspace; idle borders are that colour
blended toward the background. The selected card has a heavy border and an accent title, so
selection never relies on colour alone, and every card carries a text tag.
