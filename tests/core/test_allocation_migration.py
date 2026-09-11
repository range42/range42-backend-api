"""Fresh and existing deployments discover the durable authoring ledger."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def test_allocation_migration_upgrades_runtime_schema_and_downgrades(tmp_path):
    database = tmp_path / "migrated.db"
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "RANGE42_DB_URL": f"sqlite+aiosqlite:///{database}"}
    def migrate(*arguments):
        completed = subprocess.run([sys.executable, "-m", "alembic", *arguments], cwd=root, env=env, capture_output=True, text=True, check=False)
        assert completed.returncode == 0, completed.stderr
    migrate("upgrade", "0004_runtime_operations")
    migrate("upgrade", "0005_allocation_reservations")
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO proxmox_hosts(id,name,api_url,node_name,token_ref,default_bridge,added_at) "
                           "VALUES ('h','fixture','https://fixture','n','test-only','vmbr0','2026-01-01')")
        connection.execute("INSERT INTO allocation_reservations VALUES ('lease','draft','h','n','test-hash','[]','2027-01-01','2026-01-01')")
        connection.commit()
    migrate("upgrade", "head")
    with sqlite3.connect(database) as connection:
        names = {row[1] for row in connection.execute("PRAGMA table_info(allocation_reservations)")}
        assert names == {"id", "project_key", "host_id", "node_name", "api_url", "token_hash", "assignments", "expires_at", "checked_at"}
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0006_deployment_allocations"
        assert connection.execute("PRAGMA foreign_key_list(allocation_reservations)").fetchone()[2] == "proxmox_hosts"
        assert {row[1] for row in connection.execute("PRAGMA table_info(deployment_allocations)")} == {
            "id", "deployment_id", "project_sha", "host_id", "api_url", "node_name", "assignments", "source_assignments", "created_at"}
        assert {row[2] for row in connection.execute("PRAGMA foreign_key_list(deployment_allocations)")} == {"deployments", "proxmox_hosts"}
        assert connection.execute("SELECT id, assignments, api_url FROM allocation_reservations").fetchone() == ("lease", "[]", None)
    migrate("downgrade", "0005_allocation_reservations")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT id, assignments FROM allocation_reservations").fetchone() == ("lease", "[]")
        assert "api_url" not in {row[1] for row in connection.execute("PRAGMA table_info(allocation_reservations)")}
    migrate("downgrade", "0004_runtime_operations")
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA table_info(allocation_reservations)").fetchall() == []
        assert connection.execute("PRAGMA table_info(deployment_allocations)").fetchall() == []
        assert "operation" in {row[1] for row in connection.execute("PRAGMA table_info(attempts)")}
