"""Maintenance keeps admission, SQLite and provisioning locked until replacement."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import importlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from app.core.maintenance import MaintenanceGate
from app.core.runner_detached import record_process_identity


def guard_module():
    assert importlib.util.find_spec("app.core.maintenance_guard") is not None, "upgrades need a held admission and deployment-state guard"
    return importlib.import_module("app.core.maintenance_guard")


@pytest.fixture
def installed(tmp_path):
    root = tmp_path / "workspaces"
    root.mkdir(mode=0o700)
    database = root / ".range42.db"
    with sqlite3.connect(database) as db:
        db.executescript("""CREATE TABLE attempts (id TEXT, pid INTEGER, artifact_dir TEXT, deployment_id TEXT, state TEXT);
CREATE TABLE deployments (id TEXT, workspace_path TEXT);
CREATE TABLE workspace_locks (deployment_id TEXT);
""")
    gate = MaintenanceGate(tmp_path / "maintenance.lock")
    proof = gate.capability()
    return root, database, gate, proof


@contextmanager
def held(installed, **overrides):
    root, database, gate, proof = installed
    with guard_module().hold_maintenance(proof, gate_path=gate.path,
                                         database=database, workspace_root=root, **overrides):
        yield


def test_idle_guard_holds_all_three_locks_until_context_exit(installed):
    root, database, gate, _ = installed
    with held(installed):
        assert gate.acquire_shared() is None
        with sqlite3.connect(database, timeout=0) as db:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                db.execute("INSERT INTO workspace_locks VALUES ('new-reservation')")
        descriptor = os.open(root / ".locks/provisioning.lock", os.O_RDONLY)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(descriptor)
    descriptor = gate.acquire_shared()
    assert descriptor is not None
    os.close(descriptor)
    with sqlite3.connect(database, timeout=0) as db:
        db.execute("INSERT INTO workspace_locks VALUES ('after-maintenance')")


@pytest.mark.parametrize("table,values", [
    ("attempts", "'a', NULL, NULL, 'd', 'running'"),
    ("attempts", "'a', NULL, NULL, 'd', NULL"),
    ("attempts", "'a', NULL, NULL, 'd', 'unknown'"),
    ("workspace_locks", "'d'"),
])
def test_recorded_activity_refuses_and_releases_admission(installed, table, values):
    _, database, gate, _ = installed
    with sqlite3.connect(database) as db:
        db.execute(f"INSERT INTO {table} VALUES ({values})")
    with pytest.raises(ValueError, match="active|lock"):
        with held(installed):
            pytest.fail("active deployment cannot be replaced")
    descriptor = gate.acquire_shared()
    assert descriptor is not None
    os.close(descriptor)


def test_terminal_attempt_with_live_verified_runner_is_not_idle(installed):
    root, database, _, _ = installed
    workspace = root / "demo"
    artifact = workspace / "runner" / "attempt"
    artifact.mkdir(parents=True)
    runner = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        assert record_process_identity(artifact, runner.pid)
        (artifact / "pid").write_text(str(runner.pid))
        with sqlite3.connect(database) as db:
            db.execute("INSERT INTO deployments VALUES (?, ?)", ("d", str(workspace)))
            db.execute("INSERT INTO attempts VALUES (?, ?, ?, ?, ?)",
                       ("attempt", runner.pid, str(artifact), "d", "cancelled"))
        with pytest.raises(ValueError, match="runner"):
            with held(installed):
                pytest.fail("terminal rows do not prove the runner stopped")
    finally:
        runner.terminate()
        runner.wait(timeout=5)


@pytest.mark.parametrize("kind", ["malformed", "escape", "orphan"])
def test_unknown_artifacts_fail_closed(installed, tmp_path, kind):
    root, _, _, _ = installed
    directory = root / "demo/runner/attempt"
    directory.parent.mkdir(parents=True)
    if kind == "escape":
        directory.symlink_to(tmp_path)
    else:
        directory.mkdir()
        (directory / "pid").write_text(str(os.getpid()))
        if kind == "malformed":
            (directory / "process.json").write_text("[]")
    with pytest.raises(ValueError, match="artifact|identity"):
        with held(installed):
            pytest.fail("unknown runner artifacts cannot be declared idle")


@pytest.mark.parametrize("field", ["process", "lock", "protocol", "enabled"])
def test_changed_or_disabled_capability_cannot_authorize_stop(installed, field):
    root, database, gate, proof = installed
    changed = json.loads(json.dumps(proof))
    if field == "process":
        changed[field]["start_time"] = "0"
    elif field == "lock":
        changed[field]["inode"] += 1
    elif field == "protocol":
        changed[field] = "unknown"
    else:
        changed[field] = False
    with pytest.raises(ValueError, match="capability|identity"):
        with guard_module().hold_maintenance(changed, gate_path=gate.path,
                                              database=database, workspace_root=root):
            pytest.fail("the current process and lock must match the authenticated proof")


@pytest.mark.parametrize("payload", ["not-json-private-value", '"not-json-private-value"', '{"token":"not-json-private-value"}'])
def test_helper_refusal_does_not_echo_proof_content(payload):
    result = subprocess.run([sys.executable, "-m", "app.core.maintenance_guard"], input=payload + "\n",
                            capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert json.loads(result.stdout)["status"] == "refused"
    assert "not-json-private-value" not in result.stdout + result.stderr


def test_invalid_database_is_not_reinitialized_and_releases_gate(installed):
    _, database, gate, _ = installed
    database.write_text("invalid database, preserve for investigation")
    with pytest.raises(sqlite3.DatabaseError):
        with held(installed):
            pytest.fail("unknown state cannot be migrated during maintenance audit")
    assert database.read_text() == "invalid database, preserve for investigation"
    descriptor = gate.acquire_shared()
    assert descriptor is not None
    os.close(descriptor)
