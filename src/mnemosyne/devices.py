"""Keep the devices table in step with the config file."""

import sqlite3
import time

from mnemosyne.config import Config


def sync(conn: sqlite3.Connection, config: Config) -> None:
    now = time.time()
    for device in config.devices.values():
        conn.execute(
            "INSERT INTO devices (id, os, home, created_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (id) DO UPDATE SET os = excluded.os, home = excluded.home",
            (device.id, device.os, device.home, now),
        )
        for machine in device.machine_names:
            conn.execute(
                "INSERT INTO device_machine_names (machine_name, device_id) VALUES (?, ?)"
                " ON CONFLICT (machine_name) DO UPDATE SET device_id = excluded.device_id",
                (machine, device.id),
            )
