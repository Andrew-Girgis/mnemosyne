# Operations

## Secrets (`s`)

Project store (`.senv` in this repo, git-ignored): `WAKAPI_PASSWORD_SALT`,
`MNEMOSYNE_WAKAPI_API_KEY`, `MNEMOSYNE_TOKEN` (the server machine's user token). `bin/compose`
injects the first two into Compose; without [`s`](https://github.com/tobi/s), export
them in your shell instead. `s` redacts secret values from child output, so it cannot feed a
secret to another program's stdout (e.g. `wakatime-cli`'s `api_key_vault_cmd`).

Non-secret local settings (Wakapi's public URL, a local Wakapi build context) live in
`deploy/local.env` (git-ignored); see `deploy/local.env.example`.

## Tokens

```sh
bin/compose exec -T server mnemosyne-admin token create --kind user --device laptop \
  | tr -d '\n' | s set MNEMOSYNE_TOKEN --stdin      # prints nothing; token goes straight to s
bin/compose exec -T server mnemosyne-admin token list
bin/compose exec -T server mnemosyne-admin token revoke tok_…
```

`--device` must match a `[devices.*]` table in `config/mnemosyne.toml`. Kinds: `user` (you),
`agent` (coding agents: projects, tasks, ideas), `collector` (scans only).

## WakaTime clients

Every machine: `~/.wakatime.cfg` (mode 600) with `api_url = https://<wakapi-host>/api` and the
Wakapi API key. After rotating the Wakapi key: update `s set MNEMOSYNE_WAKAPI_API_KEY -f`,
`bin/compose up -d`, and rewrite each machine's `~/.wakatime.cfg`.

**Before sending a test heartbeat**, check `wakatime-cli --offline-count`: sending one flushes
the offline queue and wakatime-cli stamps the test's plugin label (and fills missing project
names) onto every flushed heartbeat.

**AI sync:** agent sessions only reach Wakapi when something runs wakatime-cli, so a machine
where no editor plugin fires goes silent. Systemd user timer `wakatime-ai-sync.timer` runs
`wakatime-cli --sync-ai-activity` 2 min after boot and every 5 min after, parsing Claude Code,
Codex, pi, … transcripts. Unit files are in `deploy/`; install with
`cp deploy/wakatime-ai-sync.* ~/.config/systemd/user/ && systemctl --user daemon-reload &&
systemctl --user enable --now wakatime-ai-sync.timer` (enable lingering with
`loginctl enable-linger` so it runs without a login). Check:
`systemctl --user list-timers wakatime-ai-sync.timer`.

**AI sync watchdog:** after an outage wakatime-cli can stay in backoff and stop parsing agent
transcripts while the timer still exits 0. `bin/ai-sync-watchdog` (hourly,
`deploy/ai-sync-watchdog.*`) compares the newest Claude/Codex transcript write with
`ai_logs_last_parsed_at` in `~/.wakatime/wakatime-internal.cfg`. More than 60 min behind: it
starts one `wakatime-ai-sync.service` run; still behind an hour later: desktop notification and
a failed unit. Install like the sync units (`cp deploy/ai-sync-watchdog.* …`, enable the timer).
The Wakapi dashboard's Machines panel shows the same condition per machine ("sync stalled").

The backup and watchdog units run `%h/Projects/mnemosyne/bin/…`; edit `ExecStart` if you
cloned the repo elsewhere.

## Backups

`bin/backup` takes consistent online snapshots of both databases into
`~/.local/share/mnemosyne/backups` (mode 700, override with `MNEMOSYNE_BACKUP_DIR`): `daily/`
(14 kept), `weekly/` (8 kept), `latest/` (a single copy to sync off the machine, capped at
1 GB). Each snapshot passes `PRAGMA integrity_check` before it is written.

- **Schedule:** systemd user timer `mnemosyne-backup.timer`, nightly at 03:30 (+ up to 10 min),
  `Persistent=true`. Unit files are in `deploy/`; install with
  `cp deploy/mnemosyne-backup.* ~/.config/systemd/user/ && systemctl --user daemon-reload &&
  systemctl --user enable --now mnemosyne-backup.timer`. The unit needs the user manager to be
  in the `docker` group, which only happens after a reboot following `usermod -aG docker`.
- **Off-machine copy:** point Syncthing (send-only) or any file sync at `backups/latest`.
- **Check:** `systemctl --user list-timers mnemosyne-backup.timer` and
  `journalctl --user -u mnemosyne-backup.service`.

## Restore

```sh
bin/restore ~/.local/share/mnemosyne/backups/daily/2026-09-26
```

Stops the stack, keeps the current databases inside the volumes as `*.pre-restore`, writes the
snapshot, starts the stack. Afterwards run `mnemosyne health` and compare
`mnemosyne projects list` with what you expect. To verify a snapshot without restoring, gunzip
it somewhere temporary and open it with `sqlite3`.
