"""Durable ownership of temporary credentials belonging to one runner attempt."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile

from app.core.logging import get_logger
from app.core.runner_detached import process_matches
from app.core.scenario_runtime import RuntimeVaultPlaceholder, cleanup_runtime_vault
from app.core.ssh_agent import SshAgentHandle, cleanup_agent
from app.core.workspace import shred_envvars

logger = get_logger(__name__)


def cleanup_attempt_output(workspace: Path, artifact: Path) -> dict[str, int | bool]:
    """Remove raw output only after exit and durable canonical event receipts.

    Unprocessed files remain private for operator review. Keep rc/status/PID
    evidence and canonical logs; never follow links or overwrite raw files.
    """
    report: dict[str, int | bool] = {"removed_events": 0, "retained_files": 0, "stdout_removed": False}
    try:
        resolved = artifact.resolve()
        if (artifact.is_symlink() or resolved.parent != (workspace / "runner").resolve()
                or not resolved.is_relative_to(workspace.resolve())):
            return report
        with os.fdopen(os.open(artifact / "rc", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)) as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return report
            int(stream.read(32))  # A completed runner publishes its exit code.
        try:
            pid = int((artifact / "pid").read_text())
        except (OSError, ValueError):
            pid = 0
        if process_matches(artifact, pid):
            return report
        receipts = set()
        with os.fdopen(os.open(workspace / "events.jsonl", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)) as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return report
            for line in stream:
                event = json.loads(line)
                if isinstance(event, dict) and event.get("attempt_id") == artifact.name and isinstance(event.get("runner_event_id"), str):
                    receipts.add(event["runner_event_id"])
            # The watcher flushes each record; make those receipts durable
            # before removing the raw input they replace.
            os.fsync(stream.fileno())
        directory = artifact / "job_events"
        if directory.is_symlink() or not directory.is_dir():
            return report
        for path in directory.iterdir():
            if path.name in receipts and path.suffix == ".json" and stat.S_ISREG(path.lstat().st_mode):
                path.unlink()
                report["removed_events"] += 1
            else:
                report["retained_files"] += 1
        stdout = artifact / "stdout"
        if receipts and not report["retained_files"] and stdout.exists() and stat.S_ISREG(stdout.lstat().st_mode):
            stdout.unlink()
            report["stdout_removed"] = True
        if report["retained_files"]:
            logger.warning("attempt_raw_output_retained", attempt_id=artifact.name,
                           retained_files=report["retained_files"])
    except (OSError, ValueError, RuntimeError):
        logger.warning("attempt_raw_output_cleanup_incomplete", attempt_id=artifact.name)
    return report


def record_attempt_cleanup(artifact: Path, *, ssh_agent: SshAgentHandle | None,
                           runtime_vault: RuntimeVaultPlaceholder | None) -> None:
    """Persist identities before launch, without passwords, keys or free paths."""
    document = {
        "version": 1,
        "ssh_agent": ssh_agent.identity if ssh_agent is not None else None,
        "ssh_socket": getattr(ssh_agent, "socket_ownership", None),
        "runtime_vault": ({"device": runtime_vault.device, "inode": runtime_vault.inode}
                          if runtime_vault is not None else None),
    }
    fd, temporary = tempfile.mkstemp(prefix=".cleanup-", dir=artifact)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(document, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, artifact / "cleanup.json")
    finally:
        Path(temporary).unlink(missing_ok=True)


def cleanup_attempt_credentials(workspace: Path, artifact: Path) -> None:
    """Call only after runner exit, before releasing the workspace lock."""
    cleanup_attempt_output(workspace, artifact)
    metadata = artifact / "cleanup.json"
    document = {}
    try:
        with os.fdopen(os.open(metadata, os.O_RDONLY | os.O_NOFOLLOW)) as stream:
            document = json.loads(stream.read(16384))
        if not isinstance(document, dict) or document.get("version") != 1:
            raise ValueError("invalid cleanup metadata")
    except FileNotFoundError:
        pass  # Older attempts predate persisted resource ownership.
    except (OSError, ValueError):
        logger.warning("attempt_cleanup_metadata_invalid", artifact=artifact.name)
        document = {}
    identity = document.get("ssh_agent")
    if isinstance(identity, dict):
        cleanup_agent(identity, workspace, document.get("ssh_socket"))
    vault = document.get("runtime_vault")
    if (isinstance(vault, dict) and type(vault.get("device")) is int
            and type(vault.get("inode")) is int):
        cleanup_runtime_vault(RuntimeVaultPlaceholder(
            workspace / "secrets/default_vault.yml", vault["device"], vault["inode"],
        ))
    for relative in ("env/envvars", "env/extravars", "command", "redaction.json",
                     "native-context/prepare-vars.json", "native-context/variables.json"):
        shred_envvars(artifact / relative)
    # Ownership metadata holds no credentials. Unlinking also safely removes
    # a replaced symlink without overwriting a file outside this attempt.
    try:
        metadata.unlink(missing_ok=True)
    except OSError:
        pass
