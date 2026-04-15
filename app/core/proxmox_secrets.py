"""Workspace-bound Proxmox token provisioning.

Encrypts the token body with ansible-vault using the deployment's
vault_pass.txt. The detached runner reads it via
ANSIBLE_VAULT_PASSWORD_FILE at runtime. Rotation is overwrite.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import yaml


def _write_plain(path: Path, body: dict) -> None:
    path.write_text(yaml.safe_dump(body, sort_keys=True))


def _vault_encrypt_inplace(path: Path, vault_password_file: Path) -> None:
    subprocess.run(
        ["ansible-vault", "encrypt", "--vault-password-file",
         str(vault_password_file), str(path)],
        check=True, capture_output=True, text=True,
    )


def provision_proxmox_token(*, workspace: Path, host_id: str, api_url: str,
                            token_id: str, token_secret: str,
                            vault_password_file: Path) -> Path:
    target = Path(workspace) / "secrets" / "proxmox_token.yml"
    target.parent.mkdir(parents=True, exist_ok=True)
    _write_plain(target, {
        "proxmox_host_id": host_id,
        "proxmox_api_url": api_url,
        "proxmox_api_token_id": token_id,
        "proxmox_api_token_secret": token_secret,
    })
    _vault_encrypt_inplace(target, vault_password_file)
    target.chmod(0o600)
    return target


def rotate_proxmox_token(*, workspace: Path, host_id: str, api_url: str,
                         token_id: str, token_secret: str,
                         vault_password_file: Path) -> Path:
    target = Path(workspace) / "secrets" / "proxmox_token.yml"
    if target.exists():
        target.unlink()
    return provision_proxmox_token(
        workspace=workspace, host_id=host_id, api_url=api_url,
        token_id=token_id, token_secret=token_secret,
        vault_password_file=vault_password_file,
    )
