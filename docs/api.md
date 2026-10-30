# API and CLI contract

Base URL: wherever you publish the server (the CLI reads `MNEMOSYNE_URL` or
`~/.config/mnemosyne/client.toml`). Auth: `Authorization:
Bearer <token>`; tokens are per device and have a kind.

| Endpoint | Token kinds | Notes |
| --- | --- | --- |
| `GET /health` | none | Server status and the last Wakapi import run. |
| `GET /v1/config/scan` | user, collector | Workspace roots expanded for the token's device. |
| `POST /v1/scans` | user, collector | `{complete, entries[], links[]}` from a device scan. |
| `GET /v1/projects?workspace=&lifecycle=` | user, agent | Adds `started_at`/`started_source` (`started-v1`), all-time `editor_/agent_/any_seconds`, `last_activity_at`. |
| `GET /v1/projects/{id or key}` | user, agent | Project with locations and aliases. Old keys resolve. |
| `POST /v1/projects/{id or key}/merge` | **user** | `{into}`; the source key becomes an alias. |
| `PATCH /v1/projects/{id or key}` | user, agent | `{lifecycle?, description?}`; lifecycle is user-only. |
| `GET /v1/projects/{id or key}/board` | user, agent | Tasks by column (with `started_at`/`paused_at`/`done_at` from the audit log), coding today/week/all, commits, source idea. |
| `GET /v1/ideas?status=open\|started\|dropped\|all&workspace=` | user, agent | Newest first. |
| `POST /v1/ideas` | user, agent | `{title, workspace, summary?, body?}` |
| `GET /v1/ideas/{id}` | user, agent | Idea, the project it became, edit history. |
| `PATCH /v1/ideas/{id}` | user, agent | `{title?, summary?, body?, workspace?}` |
| `POST /v1/ideas/{id}/drop`, `/reopen` | **user** | Soft and reversible. |
| `POST /v1/ideas/{id}/start` | user, agent | `{project}`: link an open idea to the project it became; an empty project description takes the idea's summary and body. |
| `POST /v1/imports/wakapi?from_start=` | **user** | Run an import now. |
| `GET /v1/coding/summary?day=YYYY-MM-DD` | user, agent | Per-project editor/agent/any seconds (`coding-duration-v1`). |
| `GET /v1/heartbeats?day=&project=` | **user** | Raw heartbeats with their derivations. |

Errors are always `{"error": {"code": "...", "message": "..."}}`.

## CLI

`mnemosyne` prints JSON on stdout and errors (same JSON shape) on stderr.

| Exit | Meaning |
| --- | --- |
| 0 | ok |
| 2 | usage error |
| 3 | unauthenticated or forbidden (includes missing `MNEMOSYNE_TOKEN`) |
| 4 | not found |
| 5 | server unreachable |
| 6 | validation, conflict or upstream (Wakapi) error |
| 1 | anything else |

`mnemosyne ideas start <idea>` creates the Try folder **on the calling device**: it asks
`try exec . <name>` (with `TRY_PATH` set to the workspace's Try root) where the new folder
goes, creates only that directory, reports it as a partial scan and links the idea.
`--project <key>` links to an existing project instead.

Environment: `MNEMOSYNE_URL` (else `url` in `~/.config/mnemosyne/client.toml`),
`MNEMOSYNE_TOKEN` / `MNEMOSYNE_AGENT_TOKEN` (inject with `s`, or export them).
`no_url` and `bad_config` errors exit 2.

## Agent callers

The CLI marks a request as coming from an agent when it detects a harness: currently
`CLAUDECODE` (+ `CLAUDE_CODE_SESSION_ID`). Codex, OpenCode and Pi markers are verified in
milestone 2 — note that `OPENCODE_EXPERIMENTAL` can be set in normal shells, so it is not a
marker. `MNEMOSYNE_ACTOR_HARNESS` / `MNEMOSYNE_ACTOR_SESSION` override detection. Agent
identity is sent as `X-Mnemosyne-Harness` / `X-Mnemosyne-Session` and recorded in the audit
log; what an agent may do is enforced by its token kind (milestone 2 adds task operations).
