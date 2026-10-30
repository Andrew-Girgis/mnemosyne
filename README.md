# Mnemosyne

A self-hosted activity and project tracker for one person across several machines. It answers:
*what did I work on, for roughly how long, with which editors and coding agents, and what state
is each project and task in?* Totals are approximations; the evidence behind them is kept.

- **Coding time** comes from [Wakapi](https://github.com/Andrew-Girgis/wakapi) (a
  WakaTime-compatible server), split into editor time and agent time (Claude Code, Codex,
  OpenCode, Pi, …).
- **Projects** are the folders in your workspaces, discovered on each machine, with Git
  commits as evidence. The same synced folder on two machines is one project.
- **Tasks and ideas** live on a Kanban board per project, editable by you and, with their own
  token, by your coding agents.
- **Interfaces:** a JSON CLI, a terminal UI, an agent skill, and an
  [Omarchy bar widget](https://github.com/Andrew-Girgis/omarchy-mnemosyne).

## How it fits together

| Piece | Where | How |
| --- | --- | --- |
| Mnemosyne API server + SQLite | one always-on machine | Docker Compose, `127.0.0.1:3110` |
| Wakapi ([fork](https://github.com/Andrew-Girgis/wakapi)) + SQLite | same machine | Docker Compose, `127.0.0.1:3100` |
| WakaTime clients (editor and agent plugins) | every machine | `~/.wakatime.cfg` → Wakapi |
| `mnemosyne` CLI and TUI | every machine | talks to the API with a per-device token |

Both ports bind to localhost. To reach them from other machines, publish them privately, for
example over [Tailscale](https://tailscale.com/kb/1312/serve) with `tailscale serve`. Nothing
needs to be on the public internet. More in [architecture](docs/architecture.md).

## Requirements

- Server: Linux or macOS with Docker and Docker Compose (with Git build contexts, the default
  on current Docker).
- Clients: Python 3.12+ and [uv](https://docs.astral.sh/uv/).
- Optional: [`s`](https://github.com/tobi/s) for secrets (otherwise use environment variables),
  [`try`](https://github.com/tobi/try) for `mnemosyne ideas start`.

## Install the server

```sh
git clone https://github.com/Andrew-Girgis/mnemosyne ~/Projects/mnemosyne
cd ~/Projects/mnemosyne
cp config/mnemosyne.example.toml config/mnemosyne.toml   # describe your devices and folders
cp deploy/local.env.example deploy/local.env             # Wakapi's public URL (non-secret)

# Secrets: with s ...
openssl rand -base64 32 | tr -d '\n' | s set WAKAPI_PASSWORD_SALT --stdin
# ... or without it: export WAKAPI_PASSWORD_SALT="$(openssl rand -base64 32)"

WAKAPI_ALLOW_SIGNUP=true bin/compose up -d --build   # sign-up open only for the first account
```

Open Wakapi (`http://127.0.0.1:3100`, or the URL you set in `deploy/local.env`), create your
account, copy its API key from **Settings**, then:

```sh
s set MNEMOSYNE_WAKAPI_API_KEY        # or export MNEMOSYNE_WAKAPI_API_KEY=…
bin/compose up -d                     # sign-up closed again, importer running

# Optional: reach both from your other machines over Tailscale
sudo tailscale serve --bg --https=8443 http://127.0.0.1:3100   # Wakapi
sudo tailscale serve --bg --https=8444 http://127.0.0.1:3110   # Mnemosyne
```

The first Compose build clones the Wakapi fork from GitHub. To build a local checkout instead,
set `WAKAPI_BUILD_CONTEXT=../wakapi` in `deploy/local.env`.

## Point WakaTime at Wakapi

On every machine, `~/.wakatime.cfg` (mode 600):

```ini
[settings]
api_url = https://<your-wakapi-address>/api
api_key = <your Wakapi API key>
```

Any WakaTime editor or agent plugin then reports to Wakapi, and Mnemosyne imports from it every
5 minutes. To count coding-agent sessions on machines where no editor plugin runs, install the
`wakatime-ai-sync` timer from `deploy/` (see [operations](docs/operations.md#wakatime-clients)).

## Install the CLI on each machine

```sh
uv tool install git+https://github.com/Andrew-Girgis/mnemosyne
mkdir -p ~/.config/mnemosyne
echo 'url = "https://<your-mnemosyne-address>"' > ~/.config/mnemosyne/client.toml
```

`MNEMOSYNE_URL` or `--url` override the file. Then, on the server, create a token for each
machine (`--device` must match a `[devices.*]` table in your config) and store it on that
machine without printing it:

```sh
bin/compose exec -T server mnemosyne-admin token create --kind user --device laptop \
  | tr -d '\n' | s set MNEMOSYNE_TOKEN --stdin
```

Without `s`, put the token in `MNEMOSYNE_TOKEN` through your shell or password manager. Agents
get their own token (`--kind agent`, stored as `MNEMOSYNE_AGENT_TOKEN`), so the server can tell
their edits apart and keep user-only actions for you.

## Use it

```sh
mnemosyne health
mnemosyne scan                                    # discover projects on this machine
mnemosyne projects list --lifecycle active
mnemosyne projects board personal/my-app          # tasks, coding time, recent commits
mnemosyne tasks create "Write the README" --project personal/my-app
mnemosyne coding summary --day 2026-09-25
mnemosyne ideas create "Garden sensor" --workspace personal
mnemosyne ideas start <idea>                      # new Try folder via `try`
mnemosyne tui                                     # Ideas | Projects | Details
```

All output is JSON; exit codes are in [the API contract](docs/api.md). Run `mnemosyne scan`
on a timer (or by hand) on each machine to keep projects current.

## Let your coding agent install and use it

The repo ships an agent skill in [`skills/mnemosyne`](skills/mnemosyne/SKILL.md). It teaches an
agent the CLI, the task workflow and what only you may do. Install it for Claude Code with:

```sh
git clone https://github.com/Andrew-Girgis/mnemosyne ~/.local/share/mnemosyne-src
ln -s ~/.local/share/mnemosyne-src/skills/mnemosyne ~/.claude/skills/mnemosyne
```

For Codex, OpenCode or Pi, link the same folder into `~/.agents/skills/`. Or paste this to your
agent:

> Install the Mnemosyne CLI from https://github.com/Andrew-Girgis/mnemosyne on this machine
> following its README: `uv tool install` it, write `~/.config/mnemosyne/client.toml` with my
> server address (ask me for it), and link `skills/mnemosyne` into your skills folder. Don't
> create or print any tokens; tell me the command to run on the server and I'll store the
> token myself. Finish by running `mnemosyne health`.

## Operations

Backups, restore, token rotation and the AI-sync watchdog are in
[docs/operations.md](docs/operations.md). Short version: `bin/backup` (nightly via
`deploy/mnemosyne-backup.timer`) and `bin/restore <snapshot-dir>`.

## Development

```sh
uv sync
uv run pytest
uv run ruff check src tests
```

## Docs

- [TUI: screens, keys, ideas → try, themes](docs/tui.md)
- [Architecture and data flow](docs/architecture.md)
- [Data model](docs/data-model.md)
- [API and CLI contract](docs/api.md)
- [Operations: tokens, backups, restore, WakaTime clients](docs/operations.md)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

MIT, see [LICENSE](LICENSE).
