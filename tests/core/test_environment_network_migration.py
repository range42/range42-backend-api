"""Optional network journals preserve existing environment revisions."""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def test_network_journal_migration_is_additive_and_refuses_discarding_ownership(
    tmp_path,
):
    database = tmp_path / "networks.db"
    root = Path(__file__).resolve().parents[2]
    environment = {**os.environ, "RANGE42_DB_URL": f"sqlite+aiosqlite:///{database}"}

    def migrate(*arguments, success=True):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *arguments],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert (result.returncode == 0) == success, result.stderr
        return result

    migrate("upgrade", "0020_native_preparation")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO proxmox_hosts(id,name,api_url,node_name,token_ref,default_bridge,added_at) VALUES ('h','host','https://fixture','node','encrypted-original','vmbr0','2026-01-01')"
        )
        connection.execute(
            "INSERT INTO native_environments(id,label,base_context_id,target_host_id,target_node,target_api_url,profile_json,review_revision,approved_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "env-original",
                "Training",
                "trusted",
                "h",
                "node",
                "https://fixture",
                "{}",
                "a" * 64,
                "administrator",
                "2026-01-01",
            ),
        )
    migrate("upgrade", "head")
    with sqlite3.connect(database) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(native_environments)")
        }
        assert "network_preparation_json" in columns, (
            "Missing durable environment-owned network preparation journal"
        )
        assert connection.execute(
            "SELECT review_revision,network_preparation_json FROM native_environments"
        ).fetchone() == ("a" * 64, None)
        connection.execute(
            "UPDATE native_environments SET network_preparation_json=?",
            (json.dumps({"original": True}),),
        )
    failure = migrate("downgrade", "0020_native_preparation", success=False)
    assert "preserve original environment network" in failure.stderr.lower()
    with sqlite3.connect(database) as connection:
        assert json.loads(
            connection.execute(
                "SELECT network_preparation_json FROM native_environments"
            ).fetchone()[0]
        ) == {"original": True}
        connection.execute(
            "UPDATE native_environments SET network_preparation_json=NULL"
        )
    migrate("downgrade", "0020_native_preparation")
