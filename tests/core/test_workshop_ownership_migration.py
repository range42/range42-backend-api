"""Upgrade preserves credentials; downgrade cannot erase original handoff intent."""

import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def test_import_upgrade_preserves_original_credentials_and_retained_intent(tmp_path):
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

    first = migrate("upgrade", "0014_deployment_admission")
    assert first.returncode == 0, first.stderr
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO proxmox_hosts(id,name,api_url,node_name,token_ref,default_bridge,added_at) VALUES ('h','host','https://fixture','node','range42:fernet:v1:unchanged','vmbr0','2026-01-01')"
        )
    upgraded = migrate("upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr
    with sqlite3.connect(database) as db:
        assert (
            db.execute("SELECT token_ref FROM proxmox_hosts").fetchone()[0]
            == "range42:fernet:v1:unchanged"
        )
        assert db.execute(
            "SELECT name FROM sqlite_master WHERE name='workshop_legacy_dispatches'"
        ).fetchone()
        db.execute(
            "INSERT INTO workshop_ownership_imports(id,deployment_id,phase,review_revision,ownership_revision,binding_revision,witness_json,changed_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "a" * 32,
                "d",
                "pending_controller",
                "b" * 64,
                "c" * 64,
                "d" * 64,
                "{}",
                "director",
                "2026-10-20",
                "2026-10-20",
            ),
        )
    refused = migrate("downgrade", "0014_deployment_admission")
    assert (
        refused.returncode != 0
        and "Original ownership handoffs exist" in refused.stderr
    )
    with sqlite3.connect(database) as db:
        assert (
            db.execute("SELECT phase FROM workshop_ownership_imports").fetchone()[0]
            == "pending_controller"
        )
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
