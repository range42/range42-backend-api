"""Exercise the packaged startup path against a real disposable SQLite database."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys

from alembic.script import ScriptDirectory
from cryptography.fernet import Fernet
import pytest


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "container_entrypoint.py"


@pytest.fixture
def container_environment(tmp_path):
    env = {"PATH": os.environ["PATH"]}
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir(mode=0o700)
    token = secret_dir / "api-token"
    token.write_text("container-test-token-" + "a" * 32)
    token.chmod(0o600)
    key = secret_dir / "credential-key"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    env.update({
        "HOME": str(tmp_path / "home"),
        "RANGE42_WORKSPACE_ROOT": str(tmp_path / "workspaces"),
        "RANGE42_AUTH_MODE": "required",
        "RANGE42_API_TOKEN_FILE": str(token),
        "RANGE42_CREDENTIAL_KEY_FILE": str(key),
        "PYTHONPATH": str(ROOT),
    })
    return env


def start(environment, code="pass"):
    assert ENTRYPOINT.is_file(), "container must validate credentials and migrate before serving"
    return subprocess.run([sys.executable, str(ENTRYPOINT), sys.executable, "-c", code],
                          cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("missing", ["RANGE42_API_TOKEN_FILE", "RANGE42_CREDENTIAL_KEY_FILE"])
def test_missing_secret_fails_before_state_initialization(container_environment, missing):
    container_environment.pop(missing)
    result = start(container_environment, "raise AssertionError('server command ran')")
    assert result.returncode != 0
    assert missing.removesuffix("_FILE") in result.stderr
    assert "server command ran" not in result.stderr
    assert not Path(container_environment["RANGE42_WORKSPACE_ROOT"]).exists()


def test_migrations_private_directories_and_state_survive_second_start(container_environment):
    code = """import json, os, sqlite3
from pathlib import Path
db = sqlite3.connect(Path(os.environ['RANGE42_WORKSPACE_ROOT']) / '.range42.db')
db.execute('CREATE TABLE IF NOT EXISTS container_probe (value INTEGER)')
db.execute('INSERT INTO container_probe VALUES (1)')
db.commit()
print(json.dumps({'count': db.execute('SELECT COUNT(*) FROM container_probe').fetchone()[0],
                  'revision': db.execute('SELECT version_num FROM alembic_version').fetchone()[0]}))
"""
    expected_revision = ScriptDirectory(str(ROOT / "alembic")).get_current_head()
    for count in (1, 2):
        result = start(container_environment, code)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"count": count, "revision": expected_revision}
    home = Path(container_environment["HOME"])
    workspace = Path(container_environment["RANGE42_WORKSPACE_ROOT"])
    for directory in (home, home / ".ssh", home / ".ssh" / "range42", home / ".ansible", workspace):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((workspace / ".range42.db").stat().st_mode) == 0o600
    with sqlite3.connect(workspace / ".range42.db") as db:
        assert {"proxmox_hosts", "allocation_reservations", "deployment_allocations", "attempts",
                "snapshot_sets", "snapshot_operations", "audit_records"}.issubset(
            {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")})


def test_invalid_key_is_not_logged_and_command_does_not_run(container_environment):
    secret = "not-a-valid-key-do-not-log-this"
    Path(container_environment["RANGE42_CREDENTIAL_KEY_FILE"]).write_text(secret)
    result = start(container_environment, "raise AssertionError('server command ran')")
    assert result.returncode != 0
    assert "Fernet" in result.stderr
    assert secret not in result.stdout + result.stderr
    assert "server command ran" not in result.stderr
    assert not Path(container_environment["RANGE42_WORKSPACE_ROOT"]).exists()


def test_failed_migration_never_executes_server(container_environment):
    workspace = Path(container_environment["RANGE42_WORKSPACE_ROOT"])
    workspace.mkdir(mode=0o700)
    (workspace / ".range42.db").write_text("invalid database")
    result = start(container_environment, "raise AssertionError('server command ran')")
    assert result.returncode != 0
    assert "server command ran" not in result.stderr
    assert (workspace / ".range42.db").read_text() == "invalid database"
