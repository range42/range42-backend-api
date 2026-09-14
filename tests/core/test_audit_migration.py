"""Audit schema changes preserve existing operational journals."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def test_audit_upgrade_preserves_snapshot_journal_and_refuses_populated_downgrade(tmp_path):
    database = tmp_path / "audit.db"
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "RANGE42_DB_URL": f"sqlite+aiosqlite:///{database}"}

    def migrate(action, target):
        return subprocess.run([sys.executable, "-m", "alembic", action, target], cwd=root, env=env,
                              capture_output=True, text=True)

    before = migrate("upgrade", "0007_snapshot_sets")
    assert before.returncode == 0, before.stderr
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO snapshot_sets VALUES ('set','dep','sha','h','digest','review','', 'native','planned','[]','2026-01-01')")
        connection.commit()
    after = migrate("upgrade", "0008_audit_records")
    assert after.returncode == 0, after.stderr
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT id FROM snapshot_sets").fetchall() == [("set",)]
        connection.execute("INSERT INTO audit_records VALUES ('record','actor','operator','POST','/v1/projects/','started',NULL,'2026-01-01',NULL)")
        connection.commit()
    result = migrate("downgrade", "0007_snapshot_sets")
    assert result.returncode != 0
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT id,state FROM audit_records").fetchall() == [("record", "started")]
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0008_audit_records"
