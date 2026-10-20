"""A gateway pin must survive upgrades and refuse destructive downgrade."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def test_foundation_migration_preserves_old_review_and_retained_gateway(tmp_path):
    database = tmp_path / 'foundation.db'
    root = Path(__file__).resolve().parents[2]
    environment = {**os.environ, 'RANGE42_DB_URL': f'sqlite+aiosqlite:///{database}'}
    def migrate(*arguments, success=True):
        result = subprocess.run([sys.executable, '-m', 'alembic', *arguments], cwd=root, env=environment,
            check=False, capture_output=True, text=True, timeout=30)
        assert (result.returncode == 0) == success, result.stderr
        return result
    migrate('upgrade', '0011_native_environments')
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO proxmox_hosts(id,name,api_url,node_name,token_ref,default_bridge,added_at) VALUES ('h','host','https://fixture','node','range42:fernet:v1:previous-ciphertext','vmbr0','2026-01-01')")
        connection.execute('INSERT INTO native_environments VALUES (?,?,?,?,?,?,?,?,?,?)',
            ('env-test','Training','trusted','h','node','https://fixture',json.dumps({'version':1}), 'a'*64,'administrator','2026-01-01'))
    migrate('upgrade', 'head')
    with sqlite3.connect(database) as connection:
        names = {row[1] for row in connection.execute('PRAGMA table_info(native_environments)')}
        assert 'foundation_json' in names, 'Missing durable gateway foundation column'
        assert connection.execute('SELECT review_revision,foundation_json FROM native_environments').fetchone() == ('a'*64, None)
        assert connection.execute('SELECT token_ref FROM proxmox_hosts').fetchone()[0] == 'range42:fernet:v1:previous-ciphertext'
        connection.execute('UPDATE native_environments SET foundation_json=?', (json.dumps({'vmid': 42130, 'owner': 'retained:gateway'}),))
    failure = migrate('downgrade', '0011_native_environments', success=False)
    assert 'preserve gateway foundation' in failure.stderr
    with sqlite3.connect(database) as connection:
        assert json.loads(connection.execute('SELECT foundation_json FROM native_environments').fetchone()[0])['vmid'] == 42130
        connection.execute('UPDATE native_environments SET foundation_json=NULL')
    migrate('downgrade', '0011_native_environments')
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT review_revision FROM native_environments').fetchone()[0] == 'a'*64
