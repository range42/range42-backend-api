"""The runtime metadata migration preserves existing deployment history."""
from importlib import reload
import sqlite3

from alembic import command
from alembic.config import Config


def test_runtime_migration_preserves_prior_attempts_and_reverses_cleanly(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    monkeypatch.setenv("RANGE42_DB_URL", f"sqlite+aiosqlite:///{path}")
    from app.core import config
    reload(config)
    alembic = Config("alembic.ini")
    command.upgrade(alembic, "0003_attempt_project_sha")
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO attempts (id,deployment_id,scope,state,event_cursor_tip,project_sha) VALUES (?,?,?,?,?,?)",
                           ("old", "dep", "full", "succeeded", 45, "a" * 40))
    command.upgrade(alembic, "0004_runtime_operations")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT state,event_cursor_tip,project_sha,operation,operation_result FROM attempts").fetchone() == (
            "succeeded", 45, "a" * 40, None, None,
        )
    command.downgrade(alembic, "0003_attempt_project_sha")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT state,event_cursor_tip,project_sha FROM attempts").fetchone() == ("succeeded", 45, "a" * 40)
