"""An upgrade preserves old deployments; populated workshop state blocks downgrade."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def test_workshop_migration_preserves_previous_state_and_private_material(tmp_path):
    database = tmp_path / 'migrated.db'
    root = Path(__file__).resolve().parents[2]
    environment = {**os.environ, 'RANGE42_DB_URL': f'sqlite+aiosqlite:///{database}'}
    def migrate(*args, success=True):
        result = subprocess.run([sys.executable, '-m', 'alembic', *args], cwd=root,
            env=environment, capture_output=True, text=True, timeout=30)
        assert (result.returncode == 0) == success, result.stderr
        return result
    migrate('upgrade', '0009_native_contexts')
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO sources VALUES ('s','github','https://github.com','none',NULL,'2026-01-01')")
        connection.execute("INSERT INTO proxmox_hosts(id,name,api_url,node_name,token_ref,default_bridge,added_at) VALUES ('h','host','https://fixture','node','fixture-only','vmbr0','2026-01-01')")
        connection.execute("INSERT INTO projects(id,name,source_id,branch_strategy,created_at,updated_at) VALUES ('p','project','s','shared_repo_subdir','2026-01-01','2026-01-01')")
        connection.execute("INSERT INTO deployments(id,codename,scenario_label,project_id,target_host_id,team_count,state,workspace_path,created_at,updated_at,native) VALUES ('d','PREVIOUS','old','p','h',1,'succeeded','/fixture','2026-01-01','2026-01-01','{}')")
    migrate('upgrade', 'head')
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT codename,state,native FROM deployments').fetchone() == ('PREVIOUS', 'succeeded', '{}')
        assert connection.execute('PRAGMA foreign_key_list(workshop_runs)').fetchone()[2] == 'deployments'
        connection.execute("INSERT INTO workshop_runs VALUES ('d','range42:fernet:v1:fixture-ciphertext','draft')")
    failure = migrate('downgrade', '0009_native_contexts', success=False)
    assert 'preserve participant material' in failure.stderr
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT credentials FROM workshop_runs').fetchone()[0] == 'range42:fernet:v1:fixture-ciphertext'
        connection.execute('DELETE FROM workshop_runs')
    migrate('downgrade', '0009_native_contexts')
    with sqlite3.connect(database) as connection:
        assert connection.execute('PRAGMA table_info(workshop_runs)').fetchall() == []
        assert connection.execute('SELECT state FROM deployments').fetchone()[0] == 'succeeded'
