"""Migration 0002: collapse duplicate proxmox_hosts before enforcing uniqueness.

Every scenario re-run POSTed the same host name and got a brand-new row, so
databases in the field carry duplicates. The unique constraint cannot be added
on top of them, and the extra rows are not inert: deployments hold a FK to
whichever duplicate happened to exist at create time.
"""
import sqlite3
from importlib import reload

import pytest


def _alembic_config():
    from alembic.config import Config

    return Config("alembic.ini")


@pytest.fixture
def db_at_0001(tmp_path, monkeypatch):
    """A database migrated to the revision that predates the constraint."""
    from alembic import command

    db = tmp_path / "m.db"
    monkeypatch.setenv("RANGE42_DB_URL", f"sqlite+aiosqlite:///{db}")
    monkeypatch.setenv("RANGE42_WORKSPACE_ROOT", str(tmp_path))
    from app.core import config as cfg

    reload(cfg)
    command.upgrade(_alembic_config(), "0001_v1_initial")
    return db


def _insert_host(conn, host_id, name, added_at, token):
    conn.execute(
        "INSERT INTO proxmox_hosts (id, name, api_url, node_name, token_ref,"
        " default_bridge, added_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (host_id, name, "https://pve:8006", "pve", token, "vmbr0", added_at),
    )


def _insert_deployment(conn, dep_id, codename, target_host_id):
    conn.execute(
        "INSERT INTO deployments (id, codename, scenario_label, project_id,"
        " target_host_id, team_count, state, workspace_path, created_at,"
        " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            dep_id,
            codename,
            "demo_lab",
            "proj-1",
            target_host_id,
            1,
            "planned",
            f"/tmp/{codename}",
            "2026-07-01 00:00:00",
            "2026-07-01 00:00:00",
        ),
    )


def test_upgrade_keeps_the_oldest_duplicate_and_repoints_deployments(db_at_0001):
    from alembic import command

    with sqlite3.connect(db_at_0001) as conn:
        _insert_host(conn, "oldest", "pve01", "2026-06-01 00:00:00", "tok-1")
        _insert_host(conn, "middle", "pve01", "2026-07-02 00:00:00", "tok-2")
        _insert_host(conn, "newest", "pve01", "2026-08-01 00:00:00", "tok-3")
        _insert_host(conn, "other", "pve02", "2026-06-15 00:00:00", "tok-4")
        # Deployments created against the later duplicates must not be stranded.
        _insert_deployment(conn, "dep-a", "alpha", "middle")
        _insert_deployment(conn, "dep-b", "bravo", "newest")
        _insert_deployment(conn, "dep-c", "charlie", "other")
        conn.commit()

    command.upgrade(_alembic_config(), "head")

    with sqlite3.connect(db_at_0001) as conn:
        hosts = dict(conn.execute("SELECT name, id FROM proxmox_hosts").fetchall())
        deployments = dict(
            conn.execute("SELECT id, target_host_id FROM deployments").fetchall()
        )

    assert hosts == {"pve01": "oldest", "pve02": "other"}
    assert deployments == {"dep-a": "oldest", "dep-b": "oldest", "dep-c": "other"}


def test_upgrade_rejects_a_second_host_under_an_existing_name(db_at_0001):
    from alembic import command

    with sqlite3.connect(db_at_0001) as conn:
        _insert_host(conn, "h1", "pve01", "2026-06-01 00:00:00", "tok-1")
        conn.commit()

    command.upgrade(_alembic_config(), "head")

    with sqlite3.connect(db_at_0001) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            _insert_host(conn, "h2", "pve01", "2026-06-02 00:00:00", "tok-2")


def test_upgrade_is_a_no_op_on_a_database_with_no_duplicates(db_at_0001):
    from alembic import command

    with sqlite3.connect(db_at_0001) as conn:
        _insert_host(conn, "h1", "pve01", "2026-06-01 00:00:00", "tok-1")
        _insert_host(conn, "h2", "pve02", "2026-06-02 00:00:00", "tok-2")
        _insert_deployment(conn, "dep-a", "alpha", "h1")
        conn.commit()

    command.upgrade(_alembic_config(), "head")

    with sqlite3.connect(db_at_0001) as conn:
        hosts = dict(conn.execute("SELECT name, id FROM proxmox_hosts").fetchall())
        deployments = dict(
            conn.execute("SELECT id, target_host_id FROM deployments").fetchall()
        )

    assert hosts == {"pve01": "h1", "pve02": "h2"}
    assert deployments == {"dep-a": "h1"}
