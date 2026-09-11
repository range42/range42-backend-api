"""Hold admission and state locks while an operator replaces this container."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys

from app.core.config import settings
from app.core.locks import ProvisioningLock
from app.core.maintenance import MaintenanceGate, PROTOCOL
from app.core.runner_detached import _process_identity, process_matches


_TERMINAL = ("succeeded", "completed", "partial", "failed", "cancelled")


def _proof_matches(proof: dict, gate: MaintenanceGate) -> None:
    if (not isinstance(proof, dict) or proof.get("protocol") != PROTOCOL
            or proof.get("enabled") is not True or not isinstance(proof.get("process"), dict)):
        raise ValueError("Installed maintenance capability is missing or unsupported")
    process = proof["process"]
    pid = process.get("pid")
    if type(pid) is not int or _process_identity(pid) != process:
        raise ValueError("API process identity changed since its maintenance capability was read")
    if gate.capability()["lock"] != proof.get("lock"):
        raise ValueError("Maintenance lock identity changed since its capability was read")


def _read_artifact(path: Path, limit: int) -> str:
    if path.is_symlink():
        raise ValueError("Runner artifact identity cannot contain symbolic links")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("Runner artifact identity is not a bounded regular file")
        with os.fdopen(descriptor, "r", closefd=False) as stream:
            return stream.read(limit + 1)
    finally:
        os.close(descriptor)


def assert_idle(db: sqlite3.Connection, workspace_root: Path) -> None:
    placeholders = ",".join("?" for _ in _TERMINAL)
    if db.execute(f"SELECT 1 FROM attempts WHERE state IS NULL OR state NOT IN ({placeholders}) LIMIT 1", _TERMINAL).fetchone():
        raise ValueError("An active or unknown attempt is still recorded")
    if db.execute("SELECT 1 FROM workspace_locks LIMIT 1").fetchone():
        raise ValueError("A workspace lock is still held")
    rows = db.execute("SELECT a.id, a.pid, a.artifact_dir, d.workspace_path FROM attempts a "
                      "LEFT JOIN deployments d ON d.id = a.deployment_id LIMIT 100001").fetchall()
    if len(rows) > 100000:
        raise ValueError("Installed attempt history exceeds the maintenance audit bound")
    artifacts: dict[Path, tuple[int | None, bool]] = {}
    for attempt, pid, recorded, workspace in rows:
        if not workspace or not isinstance(attempt, str) or Path(attempt).name != attempt:
            raise ValueError("Installed attempt artifact ownership is inconsistent")
        directory = Path(workspace) / "runner" / attempt
        if (directory != directory.resolve() or not directory.is_relative_to(workspace_root)
                or (recorded and Path(recorded) != directory)):
            raise ValueError("Installed runner artifact path is inconsistent or outside its workspace")
        artifacts[directory] = (pid, True)
    for count, workspace in enumerate(workspace_root.iterdir()):
        if count >= 100000:
            raise ValueError("Installed workspace entries exceed the maintenance audit bound")
        runner = workspace / "runner"
        if not runner.exists() and not runner.is_symlink():
            continue
        if workspace.is_symlink() or runner.is_symlink() or not runner.is_dir():
            raise ValueError("Installed runner artifact root cannot be verified")
        for directory in runner.iterdir():
            if directory.is_symlink() or not directory.is_dir():
                raise ValueError("Installed runner artifact path cannot be verified")
            artifacts.setdefault(directory, (None, False))
            if len(artifacts) > 100000:
                raise ValueError("Installed runner artifacts exceed the maintenance audit bound")
    for directory, (pid, recorded) in artifacts.items():
        if directory.is_symlink() or not directory.resolve().is_relative_to(workspace_root):
            raise ValueError("Runner artifact path is outside its installed workspace")
        pid_file = directory / "pid"
        if pid_file.exists() or pid_file.is_symlink():
            try:
                file_pid = int(_read_artifact(pid_file, 64).strip())
            except (ValueError, OSError):
                raise ValueError("Runner artifact PID identity cannot be verified") from None
            if pid is not None and pid != file_pid:
                raise ValueError("Runner artifact PID identity is inconsistent")
            pid = file_pid
        identity_file = directory / "process.json"
        if not pid and not identity_file.exists() and recorded:
            continue  # A known terminal setup failure never started a runner.
        try:
            identity = json.loads(_read_artifact(identity_file, 1024))
            if (not isinstance(identity, dict) or set(identity) != {"pid", "start_time", "boot_id"}
                    or type(identity["pid"]) is not int or identity["pid"] <= 0
                    or not isinstance(identity["start_time"], str) or not identity["start_time"].isdigit()
                    or not isinstance(identity["boot_id"], str) or not identity["boot_id"]
                    or (pid is not None and pid != identity["pid"])):
                raise ValueError("invalid identity")
            pid = identity["pid"]
        except (OSError, ValueError):
            raise ValueError("Runner artifact identity is missing or malformed") from None
        if process_matches(directory, pid):
            raise ValueError("A verified detached runner is still active")


@contextmanager
def hold_maintenance(proof: dict, *, gate_path: Path, database: Path, workspace_root: Path):
    gate = MaintenanceGate(gate_path)
    _proof_matches(proof, gate)
    descriptor = gate._open()
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Finite HTTP handlers are still active; retry after they drain") from None
        if os.fstat(descriptor).st_size:
            raise ValueError('Unfinished maintenance intent requires explicit recovery')
        _proof_matches(proof, gate)
        if (database != database.resolve() or not database.is_file()
                or workspace_root != workspace_root.resolve() or not workspace_root.is_dir()):
            raise ValueError("Installed database and workspace paths cannot be verified")
        locks = workspace_root / ".locks"
        if locks != locks.resolve():
            raise ValueError("Provisioning lock path cannot contain symbolic links")
        with ProvisioningLock(locks):
            # No writes occur, but the held writer transaction prevents a new
            # reservation from committing until the old container has stopped.
            db = sqlite3.connect(database.as_uri() + "?mode=rw", uri=True, timeout=1)
            try:
                db.execute("BEGIN IMMEDIATE")
                assert_idle(db, workspace_root)
                _proof_matches(proof, gate)
                yield
            finally:
                db.rollback()
                db.close()
    finally:
        os.close(descriptor)


def main() -> None:
    try:
        line = sys.stdin.buffer.readline(16385)
        if len(line) > 16384:
            raise ValueError("Maintenance proof exceeds its size bound")
        proof = json.loads(line)
        prefix = "sqlite+aiosqlite:///"
        if not settings.db_url.startswith(prefix) or "?" in settings.db_url:
            raise ValueError("Maintenance requires the installed absolute SQLite database URL")
        database = Path(settings.db_url.removeprefix(prefix))
        if not database.is_absolute() or not settings.maintenance_lock_file:
            raise ValueError("Maintenance requires an explicitly configured persistent admission lock")
        with hold_maintenance(proof, gate_path=Path(settings.maintenance_lock_file),
                              database=database, workspace_root=settings.workspace_root):
            print(json.dumps({"status": "idle", "protocol": PROTOCOL, "process": proof["process"], "lock": proof["lock"]}), flush=True)
            # The installer keeps stdin open until controlled container stop.
            # EOF/installer failure releases every lock without changing state.
            sys.stdin.buffer.read(1)
    except Exception:
        print(json.dumps({"status": "refused", "message": "Maintenance could not prove idle owned state; the container must remain running."}), flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
