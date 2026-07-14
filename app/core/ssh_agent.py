"""In-container unlock of passphrase-protected range42 SSH keys.

The range42 deploy keys are ed25519 keys protected by a per-family passphrase
stored in the workspace vault (``secrets/default_vault.yml``, keys
``ssh_passphrase_*``). On the deployer host ``range42-context`` loads them into
an ssh-agent via an SSH_ASKPASS shim; inside the backend container there is no
such agent, so ansible-runner cannot authenticate to the VMs.

``unlock_workspace_keys`` reproduces that mechanism: decrypt the vault with the
workspace ``vault_pass.txt``, start a dedicated ssh-agent, and ``ssh-add`` every
private key found under ``ssh_keys/`` using its passphrase (via a throwaway
SSH_ASKPASS script). The returned handle exposes ``SSH_AUTH_SOCK`` to merge into
the runner's envvars and a ``close()`` to kill the agent when the attempt ends.
"""
from __future__ import annotations

import os
import stat
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.core.logging import get_logger

logger = get_logger(__name__)

# Map a private-key filename suffix to the vault field holding its passphrase.
# Order matters: the extra-student glob (``_bob_<n>``) must be checked before
# the plain ``_bob`` suffix. Mirrors range42-context._r42_passphrase_field_for_key.
_SUFFIX_TO_FIELD: tuple[tuple[str, str], ...] = (
    ("ssh_cli.root", "ssh_passphrase_px_root"),
    ("ssh_cli.jump_user", "ssh_passphrase_px_jump"),
    ("deployer-key_alice", "ssh_passphrase_deployer_admin"),
    ("student-key_bob", "ssh_passphrase_student_user"),
)

# Filenames under ssh_keys/ that are never private keys.
_NON_KEY_NAMES = {"known_hosts", "config", "authorized_keys"}


@dataclass
class SshAgentHandle:
    """A running ssh-agent with the workspace keys loaded."""

    pid: int
    env: dict[str, str] = field(default_factory=dict)
    _closed: bool = False

    def close(self) -> None:
        """Kill the agent process. Idempotent."""
        if self._closed:
            return
        self._closed = True
        try:
            subprocess.run(
                ["ssh-agent", "-k"],
                env={**self.env, "SSH_AGENT_PID": str(self.pid)},
                capture_output=True, text=True, check=False,
            )
        except OSError:
            pass
        try:
            os.kill(self.pid, 0)
            os.kill(self.pid, 15)
        except OSError:
            pass


def _passphrase_field_for(name: str) -> str | None:
    if "student-key_bob_" in name:
        return "ssh_passphrase_student_user_extra_all"
    for suffix, field_name in _SUFFIX_TO_FIELD:
        if name.endswith(suffix):
            return field_name
    return None


def _discover_private_keys(ssh_keys_dir: Path) -> list[Path]:
    if not ssh_keys_dir.is_dir():
        return []
    keys: list[Path] = []
    for p in sorted(ssh_keys_dir.rglob("*")):
        if not p.is_file():
            continue
        if p.name.endswith(".pub") or p.name in _NON_KEY_NAMES:
            continue
        keys.append(p)
    return keys


def _decrypt_vault(vault_file: Path, vault_password_file: Path) -> dict:
    r = subprocess.run(
        ["ansible-vault", "view", "--vault-password-file",
         str(vault_password_file), str(vault_file)],
        capture_output=True, text=True, check=True,
    )
    data = yaml.safe_load(r.stdout)
    return data if isinstance(data, dict) else {}


def _start_agent() -> SshAgentHandle:
    r = subprocess.run(["ssh-agent", "-s"], capture_output=True, text=True,
                       check=True)
    sock = ""
    pid = 0
    for line in r.stdout.splitlines():
        line = line.strip()
        if line.startswith("SSH_AUTH_SOCK="):
            sock = line[len("SSH_AUTH_SOCK="):].split(";", 1)[0]
        elif line.startswith("SSH_AGENT_PID="):
            pid = int(line[len("SSH_AGENT_PID="):].split(";", 1)[0])
    return SshAgentHandle(pid=pid, env={"SSH_AUTH_SOCK": sock})


def _ssh_add(key: Path, passphrase: str, agent_env: dict[str, str]) -> bool:
    """ssh-add one key, feeding the passphrase non-interactively via SSH_ASKPASS.

    A passphrase-less key ignores the askpass shim and still loads.
    """
    fd, askpass_path = tempfile.mkstemp(prefix="r42-askpass-", suffix=".sh")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write('#!/bin/sh\nprintf "%s" "$SSH_ASKPASS_PASSWORD"\n')
        os.chmod(askpass_path, stat.S_IRWXU)
        env = {
            **agent_env,
            "SSH_ASKPASS": askpass_path,
            "SSH_ASKPASS_PASSWORD": passphrase,
            "SSH_ASKPASS_REQUIRE": "force",
            "DISPLAY": os.environ.get("DISPLAY", ":0"),
        }
        r = subprocess.run(["ssh-add", str(key)], env=env,
                           stdin=subprocess.DEVNULL,
                           capture_output=True, text=True)
        return r.returncode == 0
    finally:
        try:
            os.unlink(askpass_path)
        except OSError:
            pass


def unlock_workspace_keys(
    workspace: Path, vault_password_file: Path
) -> SshAgentHandle | None:
    """Start an ssh-agent and load the workspace's private keys into it.

    :returns: A handle exposing ``SSH_AUTH_SOCK`` in ``.env``, or ``None`` when
        there is no vault or no keys to unlock (the deploy then falls back to the
        ambient ~/.ssh, preserving prior behaviour).
    """
    workspace = Path(workspace)
    vault_file = workspace / "secrets" / "default_vault.yml"
    if not vault_file.is_file():
        return None
    keys = _discover_private_keys(workspace / "ssh_keys")
    if not keys:
        return None

    try:
        passphrases = _decrypt_vault(vault_file, Path(vault_password_file))
    except (subprocess.CalledProcessError, OSError) as exc:
        logger.warning("ssh_agent vault decrypt failed", error=str(exc))
        return None

    handle = _start_agent()
    loaded = 0
    for key in keys:
        field_name = _passphrase_field_for(key.name)
        passphrase = str(passphrases.get(field_name, "")) if field_name else ""
        if _ssh_add(key, passphrase, handle.env):
            loaded += 1
        else:
            logger.warning("ssh_agent could not add key", key=key.name)
    logger.info("ssh_agent unlocked workspace keys", loaded=loaded,
                total=len(keys))
    return handle
