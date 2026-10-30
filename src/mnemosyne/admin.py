"""Server-side administration (run on the server, inside the server container).

Talks to the database directly, so it is never exposed over the network.
"""

import json
import os
import sys

import typer

from mnemosyne import auth, devices
from mnemosyne import config as config_mod
from mnemosyne.db import connect, migrate
from mnemosyne.domain import heartbeats
from mnemosyne.importers import wakapi

app = typer.Typer(no_args_is_help=True, help="Mnemosyne server administration.")
token_app = typer.Typer(no_args_is_help=True, help="Manage API tokens.")
app.add_typer(token_app, name="token")


def _open():
    config = config_mod.load(os.environ.get("MNEMOSYNE_CONFIG", "/config/mnemosyne.toml"))
    conn = connect(os.environ.get("MNEMOSYNE_DB", "/data/mnemosyne.sqlite3"))
    migrate(conn)
    devices.sync(conn, config)
    return config, conn


def _out(data) -> None:
    typer.echo(json.dumps(data, indent=2, default=str))


@app.command("migrate")
def migrate_cmd():
    """Apply pending migrations."""
    conn = connect(os.environ.get("MNEMOSYNE_DB", "/data/mnemosyne.sqlite3"))
    _out({"applied": migrate(conn)})


@token_app.command("create")
def token_create(
    kind: str = typer.Option(..., help="user | collector | agent"),
    device: str = typer.Option(..., help="device id from the config"),
    label: str = typer.Option("", help="free-text label"),
):
    """Create a token. Prints ONLY the token on stdout, so it can be piped into `s set`."""
    config, conn = _open()
    if device not in config.devices:
        typer.echo(f"unknown device {device!r}", err=True)
        raise typer.Exit(4)
    token_id, token = auth.create_token(conn, kind, device, label or None)
    typer.echo(f"created {kind} token {token_id} for {device}", err=True)
    sys.stdout.write(token + "\n")


@token_app.command("list")
def token_list():
    _, conn = _open()
    rows = conn.execute(
        "SELECT id, kind, device_id, label, created_at, revoked_at FROM api_tokens"
        " ORDER BY created_at"
    ).fetchall()
    _out([dict(r) for r in rows])


@token_app.command("revoke")
def token_revoke(token_id: str):
    _, conn = _open()
    if not auth.revoke(conn, token_id):
        typer.echo(f"no active token {token_id}", err=True)
        raise typer.Exit(4)
    _out({"revoked": token_id})


@app.command("import-wakapi")
def import_wakapi(from_start: bool = typer.Option(False, help="re-read Wakapi's whole history")):
    """Run one Wakapi import now."""
    config, conn = _open()
    try:
        result = wakapi.run(conn, config, from_start=from_start)
    except wakapi.WakapiError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(6) from exc
    result["rederived"] = heartbeats.derive_pending(conn, config, include_unmatched=True)
    _out(result)


@app.command("rederive")
def rederive(all_rows: bool = typer.Option(False, "--all", help="re-derive every heartbeat")):
    """Re-run origin/project derivation (e.g. after a rule or config change)."""
    config, conn = _open()
    if all_rows:
        conn.execute("DELETE FROM heartbeat_derivations")
    _out({"derived": heartbeats.derive_pending(conn, config, include_unmatched=True)})
