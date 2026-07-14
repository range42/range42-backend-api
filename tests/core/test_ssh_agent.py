"""Tests for app.core.ssh_agent — in-container unlock of passphrase-protected
range42 SSH keys from the workspace vault, mirroring range42-context's
SSH_ASKPASS mechanism so the backend's ansible-runner can reach the VMs.
"""
import subprocess
from pathlib import Path

import pytest
import yaml

from app.core.ssh_agent import unlock_workspace_keys


def _gen_key(path: Path, passphrase: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-N", passphrase, "-C", path.name,
         "-f", str(path)],
        check=True, capture_output=True, text=True,
    )


def _make_vault(path: Path, body: dict, vault_pass_file: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(body, sort_keys=True))
    subprocess.run(
        ["ansible-vault", "encrypt", "--vault-password-file",
         str(vault_pass_file), str(path)],
        check=True, capture_output=True, text=True,
    )


def _agent_key_count(sock: str) -> int:
    r = subprocess.run(["ssh-add", "-l"], env={"SSH_AUTH_SOCK": sock},
                       capture_output=True, text=True)
    if r.returncode != 0:
        return 0
    return len([ln for ln in r.stdout.splitlines() if ln.strip()])


def test_unlock_loads_passphrase_protected_key(tmp_path):
    ws = tmp_path / "ALPHA-demo"
    vault_pass = ws / "secrets" / "vault_pass.txt"
    vault_pass.parent.mkdir(parents=True)
    vault_pass.write_text("vaultpw")

    passphrase = "s3cret-key-pass"
    _gen_key(ws / "ssh_keys" / "backend_keys"
             / "r42.ALPHA-demo-deployer-key_alice", passphrase)
    _make_vault(ws / "secrets" / "default_vault.yml",
                {"ssh_passphrase_deployer_admin": passphrase},
                vault_pass)

    handle = unlock_workspace_keys(ws, vault_pass)
    try:
        assert handle is not None
        assert "SSH_AUTH_SOCK" in handle.env
        assert _agent_key_count(handle.env["SSH_AUTH_SOCK"]) == 1
    finally:
        if handle is not None:
            handle.close()


def test_returns_none_when_no_vault(tmp_path):
    ws = tmp_path / "A-b"
    _gen_key(ws / "ssh_keys" / "backend_keys"
             / "r42.A-b-deployer-key_alice", "pw")
    vault_pass = ws / "secrets" / "vault_pass.txt"
    vault_pass.parent.mkdir(parents=True)
    vault_pass.write_text("x")
    # No default_vault.yml -> nothing to unlock, fall back to ambient ~/.ssh.
    assert unlock_workspace_keys(ws, vault_pass) is None


def test_returns_none_when_no_keys(tmp_path):
    ws = tmp_path / "A-b"
    vault_pass = ws / "secrets" / "vault_pass.txt"
    vault_pass.parent.mkdir(parents=True)
    vault_pass.write_text("vaultpw")
    _make_vault(ws / "secrets" / "default_vault.yml",
                {"ssh_passphrase_deployer_admin": "pw"}, vault_pass)
    (ws / "ssh_keys").mkdir()
    assert unlock_workspace_keys(ws, vault_pass) is None


def test_loads_passphraseless_key(tmp_path):
    # context_ssh_keys_use_passphrase=NO: key has no passphrase, no vault field.
    ws = tmp_path / "A-b"
    vault_pass = ws / "secrets" / "vault_pass.txt"
    vault_pass.parent.mkdir(parents=True)
    vault_pass.write_text("vaultpw")
    _gen_key(ws / "ssh_keys" / "backend_keys"
             / "r42.A-b-deployer-key_alice", "")
    _make_vault(ws / "secrets" / "default_vault.yml",
                {"ssh_passphrase_deployer_admin": ""}, vault_pass)
    handle = unlock_workspace_keys(ws, vault_pass)
    try:
        assert handle is not None
        assert _agent_key_count(handle.env["SSH_AUTH_SOCK"]) == 1
    finally:
        if handle is not None:
            handle.close()


def test_loads_multiple_key_families_including_extra_student(tmp_path):
    ws = tmp_path / "ALPHA-demo"
    vault_pass = ws / "secrets" / "vault_pass.txt"
    vault_pass.parent.mkdir(parents=True)
    vault_pass.write_text("vaultpw")
    kd = ws / "ssh_keys"
    _gen_key(kd / "backend_keys" / "r42.ALPHA-demo-deployer-key_alice", "pa")
    _gen_key(kd / "student_keys" / "r42.ALPHA-demo-student-key_bob", "pb")
    _gen_key(kd / "student_keys" / "additional.students"
             / "r42.ALPHA-demo-student-key_bob_1", "pe")
    _gen_key(kd / "jump_keys" / "px.ALPHA-demo-ssh_cli.root", "pr")
    _make_vault(ws / "secrets" / "default_vault.yml", {
        "ssh_passphrase_deployer_admin": "pa",
        "ssh_passphrase_student_user": "pb",
        "ssh_passphrase_student_user_extra_all": "pe",
        "ssh_passphrase_px_root": "pr",
    }, vault_pass)
    handle = unlock_workspace_keys(ws, vault_pass)
    try:
        assert handle is not None
        assert _agent_key_count(handle.env["SSH_AUTH_SOCK"]) == 4
    finally:
        if handle is not None:
            handle.close()


def test_close_kills_agent(tmp_path):
    ws = tmp_path / "A-b"
    vault_pass = ws / "secrets" / "vault_pass.txt"
    vault_pass.parent.mkdir(parents=True)
    vault_pass.write_text("vaultpw")
    _gen_key(ws / "ssh_keys" / "backend_keys"
             / "r42.A-b-deployer-key_alice", "pw")
    _make_vault(ws / "secrets" / "default_vault.yml",
                {"ssh_passphrase_deployer_admin": "pw"}, vault_pass)
    handle = unlock_workspace_keys(ws, vault_pass)
    assert handle is not None
    sock = handle.env["SSH_AUTH_SOCK"]
    assert _agent_key_count(sock) == 1
    handle.close()
    # After close the agent socket is dead -> ssh-add -l can't list keys.
    assert _agent_key_count(sock) == 0
