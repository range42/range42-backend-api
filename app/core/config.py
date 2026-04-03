"""Centralized application configuration.

All environment variables are read here and nowhere else. The :class:`Settings`
dataclass is frozen (immutable) and instantiated once at module level as the
``settings`` singleton.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """Immutable application settings loaded from environment variables.

    Each field reads its value from the corresponding environment variable
    at instantiation time.  The ``frozen=True`` flag prevents accidental
    mutation after startup.

    :param project_root: Resolved path to the project root directory.
    :type project_root: Path
    :param wwwapp_playbooks_dir: Local playbooks directory (``API_BACKEND_WWWAPP_PLAYBOOKS_DIR``).
    :type wwwapp_playbooks_dir: str
    :param public_playbooks_dir: External playbooks repository path (``API_BACKEND_PUBLIC_PLAYBOOKS_DIR``).
    :type public_playbooks_dir: str
    :param inventory_dir: Ansible inventory directory (``API_BACKEND_INVENTORY_DIR``).
    :type inventory_dir: str
    :param vault_file: Path to the vault-encrypted variables file (``API_BACKEND_VAULT_FILE``).
    :type vault_file: str
    :param vault_password_file: Path to the Ansible Vault password file (``VAULT_PASSWORD_FILE``).
    :type vault_password_file: str
    :param vault_password: Ansible Vault password as a plain string (``VAULT_PASSWORD``).
    :type vault_password: str
    :param cors_origin_regex: Regex for allowed CORS origins (``CORS_ORIGIN_REGEX``).
    :type cors_origin_regex: str
    :param host: Server bind address (``HOST``).
    :type host: str
    :param port: Server listen port (``PORT``).
    :type port: int
    :param debug: Whether debug mode is enabled (``DEBUG``).
    :type debug: bool
    """

    project_root: Path = field(
        default_factory=lambda: Path(os.getenv("PROJECT_ROOT_DIR", ".")).resolve()
    )

    # Playbook paths
    wwwapp_playbooks_dir: str = field(
        default_factory=lambda: os.getenv("API_BACKEND_WWWAPP_PLAYBOOKS_DIR", "")
    )
    public_playbooks_dir: str = field(
        default_factory=lambda: os.getenv("API_BACKEND_PUBLIC_PLAYBOOKS_DIR", "")
    )
    inventory_dir: str = field(
        default_factory=lambda: os.getenv("API_BACKEND_INVENTORY_DIR", "")
    )
    vault_file: str = field(
        default_factory=lambda: os.getenv("API_BACKEND_VAULT_FILE", "")
    )

    # Vault credentials
    vault_password_file: str = field(
        default_factory=lambda: os.getenv("VAULT_PASSWORD_FILE", "")
    )
    vault_password: str = field(default_factory=lambda: os.getenv("VAULT_PASSWORD", ""))

    # CORS
    cors_origin_regex: str = field(
        default_factory=lambda: os.getenv(
            "CORS_ORIGIN_REGEX",
            r"^https?://(localhost|127\.0\.0\.1|\[::1\]|192\.168\.42\.\d{1,3})(:\d+)?$",
        )
    )

    # Server
    host: str = field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.getenv("PORT", "8000")))
    debug: bool = field(
        default_factory=lambda: os.getenv("DEBUG", "").lower() in ("1", "true", "yes")
    )

    @property
    def playbook_path(self) -> Path:
        """Return the default generic playbook path.

        :returns: Resolved path to ``playbooks/generic.yml`` under the project root.
        :rtype: Path
        """
        return self.project_root / "playbooks" / "generic.yml"

    @property
    def inventory_name(self) -> str:
        """Return the default inventory filename.

        :returns: The string ``"hosts"``.
        :rtype: str
        """
        return "hosts"


settings = Settings()
