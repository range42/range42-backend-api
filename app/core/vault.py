"""Vault password management. No global state — uses a class instance."""

from pathlib import Path


class VaultManager:
    """Manages the Ansible vault password file path."""

    def __init__(self) -> None:
        self._vault_pass_path: Path | None = None

    def set_vault_path(self, p: Path | None) -> None:
        self._vault_pass_path = p

    def get_vault_path(self) -> Path | None:
        return self._vault_pass_path
