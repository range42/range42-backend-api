"""Retained runtime target pins survive upgrades and prevent unsafe downgrade."""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def test_runtime_target_migration_preserves_old_reviews_and_requires_pin_recovery(
    tmp_path,
):
    database = tmp_path / "runtime.db"
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "RANGE42_DB_URL": f"sqlite+aiosqlite:///{database}"}

    def migrate(target, *, direction="upgrade", success=True):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", direction, target],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert (result.returncode == 0) == success, result.stderr
        return result

    migrate("0018_native_capacity_receipt")
    with sqlite3.connect(database) as c:
        c.execute(
            "INSERT INTO proxmox_hosts(id,name,api_url,node_name,token_ref,default_bridge,added_at) VALUES ('h','old','https://fixture','node','range42:fernet:v1:unchanged','vmbr0','2026-01-01')"
        )
        c.execute(
            "INSERT INTO native_environments(id,label,base_context_id,target_host_id,target_node,target_api_url,profile_json,review_revision,approved_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "env-old",
                "Old bound runtime",
                "base",
                "h",
                "node",
                "https://fixture",
                "{}",
                "a" * 64,
                "admin",
                "2026-01-01",
            ),
        )
    migrate("head")
    with sqlite3.connect(database) as c:
        columns = {
            row[1] for row in c.execute("PRAGMA table_info(native_environments)")
        }
        assert "runtime_target_json" in columns
        assert c.execute(
            "SELECT review_revision,runtime_target_json FROM native_environments"
        ).fetchone() == ("a" * 64, None)
        c.execute(
            "UPDATE native_environments SET runtime_target_json=?",
            (json.dumps({"version": 1, "physical_node_id": "b" * 32}),),
        )
    refused = migrate(
        "0018_native_capacity_receipt", direction="downgrade", success=False
    )
    assert "runtime target" in refused.stderr.lower()
    with sqlite3.connect(database) as c:
        assert (
            json.loads(
                c.execute(
                    "SELECT runtime_target_json FROM native_environments"
                ).fetchone()[0]
            )["physical_node_id"]
            == "b" * 32
        )
        c.execute("UPDATE native_environments SET runtime_target_json=NULL")
    migrate("0018_native_capacity_receipt", direction="downgrade")
    with sqlite3.connect(database) as c:
        assert c.execute(
            "SELECT review_revision FROM native_environments"
        ).fetchone() == ("a" * 64,)
        assert c.execute("SELECT token_ref FROM proxmox_hosts").fetchone() == (
            "range42:fernet:v1:unchanged",
        )
