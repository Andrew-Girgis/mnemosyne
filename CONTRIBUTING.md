# Contributing to Mnemosyne

Thanks for helping. Mnemosyne is a small personal tool, so issues and focused pull requests
are the best way in.

## Before you start

- **Bugs:** open an issue with what you ran, what you expected, and the JSON error the CLI
  printed. Leave out tokens, API keys and anything from your `~/.wakatime.cfg`.
- **Features:** open an issue first so we can agree on the shape before you write code.

## Set up

```sh
git clone https://github.com/Andrew-Girgis/mnemosyne
cd mnemosyne
uv sync
uv run pytest
uv run ruff check src tests
```

The tests use a throwaway SQLite file and a fake Wakapi, so you don't need Docker or a real
Wakapi account.

## Guidelines

- Keep the CLI's contract: JSON on stdout, JSON errors on stderr, the exit codes in
  [docs/api.md](docs/api.md). Update that file when you add or change an endpoint or command.
- Raw imported data is never modified. If you change how something is derived, bump the rule
  version (e.g. `project-map-v2` → `v3`) so `mnemosyne-admin rederive --all` can rebuild it.
- Add database changes as a new numbered file in `src/mnemosyne/migrations/`; never edit one
  that has shipped.
- Add tests for new behaviour next to the existing ones in `tests/`.
- Secrets come from the environment (or [`s`](https://github.com/tobi/s)), never from files in
  the repo. Don't commit `config/mnemosyne.toml`, `deploy/local.env` or `.senv`.

## Pull requests

- One change per pull request, with a short description of what and why.
- `uv run pytest` and `uv run ruff check src tests` must pass.
- By contributing you agree that your work is released under the [MIT licence](LICENSE).
