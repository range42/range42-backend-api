"""A downgrade cannot silently erase a pending or retired capacity intent."""

import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def test_capacity_upgrade_preserves_credentials_and_downgrade_retains_uncertain_hold(
    tmp_path,
):
    database = tmp_path / "migration.db"
    environment = {**os.environ, "RANGE42_DB_URL": f"sqlite+aiosqlite:///{database}"}

    def migrate(*args):
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=Path(__file__).resolve().parents[2],
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )

    original = migrate("upgrade", "0015_workshop_ownership")
    assert original.returncode == 0, original.stderr
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO proxmox_hosts(id,name,api_url,node_name,token_ref,default_bridge,added_at) VALUES ('h','host','https://fixture','node','range42:fernet:v1:unchanged','vmbr0','2026-01-01')"
        )
    upgraded = migrate("upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr
    with sqlite3.connect(database) as db:
        assert db.execute(
            "SELECT name FROM sqlite_master WHERE name='capacity_holds'"
        ).fetchone(), "Missing durable capacity migration"
        assert (
            db.execute("SELECT token_ref FROM proxmox_hosts").fetchone()[0]
            == "range42:fernet:v1:unchanged"
        )
        db.execute(
            "INSERT INTO capacity_holds(id,deployment_id,allocation_id,physical_node_id,status,record_json,record_digest,plan_revision,binding_revision,agent_revision,root_policy_revision,approved_by,revision,created_at,approval_revision) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "a" * 32,
                "deployment",
                "allocation",
                "b" * 32,
                "pending_root",
                "{}",
                "c" * 64,
                "d" * 64,
                "e" * 64,
                "f" * 64,
                "1" * 64,
                "director",
                1,
                "2026-10-20",
                "2" * 64,
            ),
        )
    refused = migrate("downgrade", "0015_workshop_ownership")
    assert refused.returncode != 0 and "Capacity commitments exist" in refused.stderr
    with sqlite3.connect(database) as db:
        assert (
            db.execute("SELECT status FROM capacity_holds").fetchone()[0]
            == "pending_root"
        )
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
