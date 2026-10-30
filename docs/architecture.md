# Architecture and data flow

```
 Laptop                                         Server (always on)
 ─────────────                                  ─────────────────────────────────────────────
 agent harnesses ─ WakaTime plugins ─┐          tailscale serve :8443 ──► Wakapi (fork)
   (offline queue in wakatime-cli)   ├─HTTPS──►                              │ SQLite volume
 mnemosyne CLI (scan, queries) ──────┘          tailscale serve :8444 ──► Mnemosyne API ◄─┘
                                                                            │  every 5 min: GET
 Server: same WakaTime plugins + CLI ─────────────────────────────────────►│  /heartbeats?date=
                                                                            ▼
                                                                  SQLite volume (mnemosyne)
                                                                  raw ─► derived ─► summaries
```

Principles the code follows (from the spec):

- **One API.** The CLI, the TUI (milestone 2), collectors (3) and Omarchy widgets (5) all go
  through the HTTP API. Only `mnemosyne-admin`, run inside the server container, opens the
  database directly (migrations, tokens, forced imports).
- **Raw vs derived.** Imported observations (`raw_heartbeats`) are immutable. Everything
  computed from them lives in separate tables and records the rule version that produced it,
  so rules can change and be re-run (`mnemosyne-admin rederive --all`) without touching
  evidence.
- **Stable source IDs.** A heartbeat's ID is a fingerprint of its content, not Wakapi's row
  ID, so reimports, restarts and a rebuilt Wakapi never double-count.
- **Device-aware projects.** Projects are keyed by workspace + path under the workspace base
  (`personal/tries/2026-04-14-ttc-gtfs-rt`), never by an absolute path, so the same synced
  Try dir on both machines is one project. Each device reports its own scan.

## Wakapi import

- Uses Wakapi's WakaTime-compatible read API only: `GET
  /api/compat/wakatime/v1/users/current/heartbeats?date=`.
- First run (and `--from-start`) walks from `history_start` (default 2013-01-01); Wakapi's
  all-time range is built from pre-computed summaries and lags behind late, old data.
- Every run re-reads the last **7** days and the first run each day re-reads **180** (Wakapi's
  default `heartbeat_max_age`), because
  WakaTime clients flush offline queues late. It also reads one day past today because
  Wakapi buckets days in the account's timezone, which may be ahead of the configured one.
- After Wakapi's own WakaTime.com history import: `mnemosyne-admin import-wakapi --from-start`.

## Derivation rules (current versions)

| Rule | What it decides |
| --- | --- |
| `origin-v2` | `agent` if the WakaTime category is `ai coding` or the plugin names a known agent harness (Claude Code, OpenCode, Codex, Pi, Hermes Agent); otherwise `editor`. |
| `project-map-v2` | Device from machine name; project from the entity path (Try roots, graduated repos, legacy `~/src/tries` alias, pre-tracking graduation by name), else a unique project-name match, else `unmatched` (kept). Distrusts project names that a known wakatime-cli test-queue flush stamped onto heartbeats. |
| `coding-duration-v1` | Within each (device, origin) stream a heartbeat counts until the next one, capped at 10 minutes (Wakapi's default timeout); overlapping intervals are unioned per project and origin, never summed. |

## Project discovery and identity

- Every non-hidden directory in a Try root or directly under a workspace base is a project,
  Git or not (Git adds commit evidence).
- A complete scan that no longer sees a location marks it gone. A new project is linked to a
  vanished one automatically when the Git root commit matches, or when a Try graduates into
  its workspace under the same name (date prefix optional). Anything else ambiguous becomes a
  `move_suggestions` row for the TUI.
- Graduation leaves a symlink in the Try root; the scanner reports it and the old key becomes
  an alias (merging any duplicate made from it).
- Only complete scans can infer moves; a partial scan (unreadable root) never does.
