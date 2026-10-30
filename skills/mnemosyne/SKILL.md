---
name: mnemosyne
description: Track project status, tasks and ideas in the user's Mnemosyne server with the `mnemosyne` CLI. Use when the user asks to log, move or check a task, record an idea, set a project description, see what they worked on or how long they coded, or when you finish a piece of work that belongs on their task board. Also use to install or connect the Mnemosyne CLI on a machine.
---

# Mnemosyne

Mnemosyne is the user's self-hosted tracker: projects (folders found on their machines),
a task board per project, an ideas list, and coding time imported from Wakapi. You talk to it
only through the `mnemosyne` CLI. Every command prints JSON on stdout; errors are JSON on stderr.

## Check the connection first

```sh
mnemosyne health    # server up, last Wakapi import
mnemosyne whoami    # must say kind "agent"; your harness and session are detected
```

| Exit | Meaning | What to do |
| --- | --- | --- |
| 2 | usage, or `no_url` / `bad_config` | Server URL missing: see "Installing" below |
| 3 | no or rejected token | Ask the user to store `MNEMOSYNE_AGENT_TOKEN` (never ask for the value) |
| 4 | not found | List projects/tasks and use the exact key or id |
| 5 | server unreachable | Tell the user; don't retry in a loop |
| 6 | validation or conflict | Read the message; fix the arguments |

## Everyday work

```sh
mnemosyne projects list --lifecycle active          # keys look like personal/my-app
mnemosyne projects board <project>                  # tasks by column, coding time, commits
mnemosyne projects describe <project> "One-line purpose"
mnemosyne tasks list --project <project> [--column Active]
mnemosyne tasks create "Title" --project <project> [--column Inbox] [--description "…"]
mnemosyne tasks move <task> Active                  # columns: Inbox, Active, Paused, Done by default
mnemosyne tasks note <task> "What changed, what is left"
mnemosyne tasks edit <task> [--title …] [--description …]
mnemosyne select <task>                             # link this agent session to the task
mnemosyne clear                                     # unlink it when you're done
mnemosyne ideas list
mnemosyne ideas create "Title" --workspace personal [--summary "…"] [--body "…"]
mnemosyne coding summary [--day YYYY-MM-DD] [--days 7]
```

Workflow when the user asks you to work on something tracked:

1. Find the project (`projects list`) and task (`tasks list --project …`); create the task if it
   does not exist.
2. `select` the task and move it to `Active`.
3. When you finish, add a `note` saying what changed and what is left, then move it to `Done`
   only if the user agrees it is done; otherwise leave it `Active` or `Paused`.

## Things only the user may do

Agent tokens cannot change a project's lifecycle (`projects lifecycle`), merge projects,
resolve move suggestions, archive tasks, or drop/reopen ideas. Tell the user the command to run
instead of trying it. Don't start the TUI (`mnemosyne tui`); it is interactive.

## Installing the CLI on a machine

Needs Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
uv tool install git+https://github.com/Andrew-Girgis/mnemosyne
mkdir -p ~/.config/mnemosyne
printf 'url = "%s"\n' "https://<server-address>" > ~/.config/mnemosyne/client.toml
```

Ask the user for the server address. Tokens are created on the server
(`bin/compose exec -T server mnemosyne-admin token create --kind agent --device <device>`)
and must go straight into a secret store or environment variable the user controls:
`MNEMOSYNE_AGENT_TOKEN` for agents, `MNEMOSYNE_TOKEN` for the user. If
[`s`](https://github.com/tobi/s) is installed, the CLI fetches the token from it on its own.
Never print, log or echo a token.
