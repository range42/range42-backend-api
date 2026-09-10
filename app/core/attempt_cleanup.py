"""Durable ownership of temporary credentials belonging to one runner attempt."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from app.core.logging import get_logger
from app.core.runner_detached import signal_process_identity
from app.core.scenario_runtime import RuntimeVaultPlaceholder, cleanup_runtime_vault
from app.core.ssh_agent import SshAgentHandle
from app.core.workspace import shred_envvars

logger = get_logger(__name__)


def record_attempt_cleanup(artifact: Path, *, ssh_agent: SshAgentHandle | None,
                           runtime_vault: RuntimeVaultPlaceholder | None) -> None:
    """Persist identities before launch, without passwords, keys or free paths."""
    document = {
        "version": 1,
        "ssh_agent": ssh_agent.identity if ssh_agent is not None else None,
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
        signal_process_identity(identity)
    vault = document.get("runtime_vault")
    if (isinstance(vault, dict) and type(vault.get("device")) is int
            and type(vault.get("inode")) is int):
        cleanup_runtime_vault(RuntimeVaultPlaceholder(
            workspace / "secrets/default_vault.yml", vault["device"], vault["inode"],
        ))
    for relative in ("env/envvars", "env/extravars", "command", "redaction.json"):
        shred_envvars(artifact / relative)
    # Ownership metadata holds no credentials. Unlinking also safely removes
    # a replaced symlink without overwriting a file outside this attempt.
    try:
        metadata.unlink(missing_ok=True)
    except OSError:
        pass
