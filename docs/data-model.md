# Data model

SQLite, migrations in `src/mnemosyne/migrations/` (applied in order at server start).
Times are UTC unix seconds; rendering uses `timezone` from the config.

## Milestone 1 tables

| Table | Kind | Purpose |
| --- | --- | --- |
| `devices`, `device_machine_names` | config mirror | Devices from the config and the hostnames clients report (a Mac may report `name.local`). |
| `api_tokens` | auth | sha256 hashes of per-device `user` / `collector` / `agent` tokens. |
| `projects` | editable | Stable `prj_…` ID, device-independent `key`, workspace, kind (`try`, `graduated`, `legacy`, `manual`), lifecycle (`active`, `paused`, `archived`). |
| `project_aliases` | editable | Old keys that still resolve (moves, graduation links, merges, legacy). |
| `project_locations` | observed | Where each device last saw each project; `gone_at` once a complete scan stops seeing it. |
| `move_suggestions` | derived | Ambiguous moves awaiting the user. |
| `tasks`, `task_notes` | editable | Created now, used from milestone 2. |
| `audit_log` | append-only | Every change to editable records with actor kind/device/harness/session. |
| `selection_events` | append-only | Per-device selection history (milestone 2/3). |
| `raw_heartbeats` | **raw, immutable** | Wakapi heartbeats as fetched, keyed by content fingerprint. |
| `heartbeat_derivations` | derived | Origin, plugin, device, project and match rule per heartbeat, with rule version. |
| `import_cursors`, `import_runs` | bookkeeping | Importer checkpoints and a log of every run. |

## Milestone 3 (TUI redesign)

| Table / column | Kind | Purpose |
| --- | --- | --- |
| `ideas` | editable | `ida_…` ideas with workspace, title, one-line summary, body and status (`open`, `started`, `dropped`). Starting links `project_id` to the project it became. |
| `projects.description` | editable | Free text; seeded from the idea a project was started from. |

Derived at read time (not stored): a project's start date (`started-v1`: the Try folder's
date prefix on its name or any alias, else the earliest of first heartbeat, first commit and
first seen) and each task's `started_at` / `paused_at` / `done_at` from its `task.move`
audit rows. Tasks have no durations yet.

## Planned (later migrations)

- Milestone 3: `raw_app_events` (device, app identity, start/end, idle flag, observed/inferred)
  and `app_catalog` (cross-OS app names + allowlist).
- Milestone 4: `agent_sessions`, `raw_agent_events` (timestamps and metadata only), per-harness
  cursors, `inferred_human_intervals`.
- Milestone 5: `attribution_intervals` (derived, rule-versioned) and `attribution_corrections`
  (explicit, user-only, never rewriting evidence).
