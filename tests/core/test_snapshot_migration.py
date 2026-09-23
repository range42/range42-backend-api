"""Snapshot journals migrate without altering deployed guests or existing leases."""

import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def test_snapshot_schema_upgrade_preserves_existing_records(tmp_path):
    database = tmp_path / "snapshot.db"
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "RANGE42_DB_URL": f"sqlite+aiosqlite:///{database}"}

    def migrate(target):
        completed = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", target],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr

    migrate("0006_deployment_allocations")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO proxmox_hosts(id,name,api_url,node_name,token_ref,default_bridge,added_at) "
            "VALUES ('h','fixture','https://fixture','n','test-only','vmbr0','2026-01-01')"
        )
        connection.commit()
    migrate("0007_snapshot_sets")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT id FROM proxmox_hosts").fetchall() == [("h",)]
        assert {
            row[1] for row in connection.execute("PRAGMA table_info(snapshot_sets)")
        } >= {
            "id",
            "deployment_id",
            "project_sha",
            "target_digest",
            "members",
            "native_name",
        }
        assert {
            row[1]
            for row in connection.execute("PRAGMA table_info(snapshot_operations)")
        } >= {"plan_digest", "plan", "members", "attempt_id", "expires_at"}
        assert {
            row[2]
            for row in connection.execute(
                "PRAGMA foreign_key_list(snapshot_operations)"
            )
        } == {"snapshot_sets", "attempts"}
        assert (
            connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            == "0007_snapshot_sets"
        )

    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO snapshot_sets VALUES ('set','dep','sha','h','digest','review','', 'native','planned','[]','2026-01-01')"
        )
        connection.commit()
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0006_deployment_allocations"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT id FROM snapshot_sets").fetchall() == [
            ("set",)
        ]
