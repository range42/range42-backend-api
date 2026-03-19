"""Centralized application configuration. All env vars read here, nowhere else."""

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """Immutable application settings loaded from environment variables."""

    project_root: Path = field(default_factory=lambda: Path(os.getenv("PROJECT_ROOT_DIR", ".")).resolve())

    # Playbook paths
    wwwapp_playbooks_dir: str = field(default_factory=lambda: os.getenv("API_BACKEND_WWWAPP_PLAYBOOKS_DIR", ""))
    public_playbooks_dir: str = field(default_factory=lambda: os.getenv("API_BACKEND_PUBLIC_PLAYBOOKS_DIR", ""))
    inventory_dir: str = field(default_factory=lambda: os.getenv("API_BACKEND_INVENTORY_DIR", ""))
    vault_file: str = field(default_factory=lambda: os.getenv("API_BACKEND_VAULT_FILE", ""))

    # Vault credentials
    vault_password_file: str = field(default_factory=lambda: os.getenv("VAULT_PASSWORD_FILE", ""))
    vault_password: str = field(default_factory=lambda: os.getenv("VAULT_PASSWORD", ""))

    # CORS
    cors_origin_regex: str = field(
        default_factory=lambda: os.getenv(
            "CORS_ORIGIN_REGEX",
            r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$",
        )
    )

    # Server
    host: str = field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.getenv("PORT", "8000")))
    debug: bool = field(default_factory=lambda: os.getenv("DEBUG", "").lower() in ("1", "true", "yes"))

    @property
    def playbook_path(self) -> Path:
        return self.project_root / "playbooks" / "generic.yml"

    @property
    def inventory_name(self) -> str:
        return "hosts"


settings = Settings()
