"""An environment binding survives upgrades and blocks unsafe downgrade."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def test_environment_migration_preserves_existing_state_and_binding(tmp_path):
    database = tmp_path / 'environment.db'
    root = Path(__file__).resolve().parents[2]
    environment = {**os.environ, 'RANGE42_DB_URL': f'sqlite+aiosqlite:///{database}'}
    def migrate(*args, success=True):
        result = subprocess.run([sys.executable, '-m', 'alembic', *args], cwd=root,
            env=environment, capture_output=True, text=True, timeout=30)
        assert (result.returncode == 0) == success, result.stderr
        return result
    migrate('upgrade', '0010_workshop_runs')
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO proxmox_hosts(id,name,api_url,node_name,token_ref,default_bridge,added_at) VALUES ('h','host','https://fixture','node','range42:fernet:v1:previous-ciphertext','vmbr0','2026-01-01')")
    migrate('upgrade', 'head')
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT token_ref FROM proxmox_hosts').fetchone()[0] == 'range42:fernet:v1:previous-ciphertext'
        assert connection.execute('PRAGMA foreign_key_list(native_environments)').fetchone()[2] == 'proxmox_hosts'
        connection.execute('INSERT INTO native_environments (id,label,base_context_id,target_host_id,target_node,target_api_url,profile_json,review_revision,approved_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
            ('env-test','Training','trusted','h','node','https://fixture',json.dumps({'version':1}), 'a'*64,'administrator','2026-01-01'))
    failure = migrate('downgrade', '0010_workshop_runs', success=False)
    assert 'preserve deployment context bindings' in failure.stderr
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT target_host_id FROM native_environments').fetchone()[0] == 'h'
        connection.execute('DELETE FROM native_environments')
    migrate('downgrade', '0010_workshop_runs')
    with sqlite3.connect(database) as connection:
        assert connection.execute('PRAGMA table_info(native_environments)').fetchall() == []
        assert connection.execute('SELECT token_ref FROM proxmox_hosts').fetchone()[0] == 'range42:fernet:v1:previous-ciphertext'
