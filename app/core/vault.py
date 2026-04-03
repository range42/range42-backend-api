"""Vault password management.

Provides :class:`VaultManager`, a simple holder for the Ansible Vault
password file path.  The path is set during the FastAPI lifespan startup
and read by the runner whenever a playbook execution requires vault
decryption.
"""

from pathlib import Path


class VaultManager:
    """Manages the Ansible vault password file path.

    Stores a single :class:`Path` reference that points to the vault
    password file on disk.  The path may be a user-supplied file
    (``VAULT_PASSWORD_FILE``) or a temp file written from
    ``VAULT_PASSWORD`` during application startup.

    :param _vault_pass_path: Internal path storage, initially ``None``.
    :type _vault_pass_path: Path or None
    """

    def __init__(self) -> None:
        """Initialize the VaultManager with no vault path set."""
        self._vault_pass_path: Path | None = None

    def set_vault_path(self, p: Path | None) -> None:
        """Set the vault password file path.

        :param p: Absolute path to the vault password file, or ``None`` to clear.
        :type p: Path or None
        """
        self._vault_pass_path = p

    def get_vault_path(self) -> Path | None:
        """Return the current vault password file path.

        :returns: The stored path, or ``None`` if not set.
        :rtype: Path or None
        """
        return self._vault_pass_path
