"""Identity and original credentials survive upgrade and unsafe downgrade refuses."""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def test_physical_node_migration_retains_original_target_and_refuses_record_loss(
    tmp_path,
):
    database = tmp_path / "physical.db"
    root = Path(__file__).resolve().parents[2]
    environment = {**os.environ, "RANGE42_DB_URL": f"sqlite+aiosqlite:///{database}"}

    def migrate(*args, success=True):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert (result.returncode == 0) == success, result.stderr
        return result

    migrate("upgrade", "0012_gateway_foundations")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO proxmox_hosts(id,name,api_url,node_name,token_ref,default_bridge,added_at) VALUES ('h','host','https://fixture','pve-test','range42:fernet:v1:unchanged','vmbr0','2026-01-01')"
        )
    migrate("upgrade", "head")
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute("SELECT token_ref FROM proxmox_hosts").fetchone()[0]
            == "range42:fernet:v1:unchanged"
        )
        connection.execute(
            "INSERT INTO physical_nodes(id,hardware_binding,node_name,created_at) VALUES (?,?,?,?)",
            ("a" * 32, "b" * 64, "pve-test", "2026-10-20"),
        )
        connection.execute(
            "INSERT INTO physical_node_bindings(host_id,physical_node_id,target_revision,agent_revision,review_revision,observation_digest,observation_json,approved_by,recorded_at,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "h",
                "a" * 32,
                *(["c" * 64] * 4),
                json.dumps({}),
                "fixture-director",
                "2026-10-20",
                "2026-10-20",
            ),
        )
    failure = migrate("downgrade", "0012_gateway_foundations", success=False)
    assert "Physical identity records exist" in failure.stderr
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT host_id,physical_node_id,approved_by FROM physical_node_bindings"
        ).fetchone() == ("h", "a" * 32, "fixture-director")
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        connection.execute("DELETE FROM physical_node_bindings")
        connection.execute("DELETE FROM physical_nodes")
    migrate("downgrade", "0012_gateway_foundations")
    with sqlite3.connect(database) as connection:
        assert not connection.execute("PRAGMA table_info(physical_nodes)").fetchall()
        assert (
            connection.execute("SELECT token_ref FROM proxmox_hosts").fetchone()[0]
            == "range42:fernet:v1:unchanged"
        )
