"""Server entry point (used by the container)."""

import logging
import os

import uvicorn

from mnemosyne import config as config_mod
from mnemosyne.server.app import create_app


def build():
    config = config_mod.load(os.environ.get("MNEMOSYNE_CONFIG", "/config/mnemosyne.toml"))
    return create_app(config, os.environ.get("MNEMOSYNE_DB", "/data/mnemosyne.sqlite3"))


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        build(),
        host=os.environ.get("MNEMOSYNE_HOST", "0.0.0.0"),
        port=int(os.environ.get("MNEMOSYNE_PORT", "8000")),
    )
