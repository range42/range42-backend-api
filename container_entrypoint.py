"""Validate container configuration, migrate persistent state, then exec the API."""
from __future__ import annotations

import os
from pathlib import Path
import sys

from alembic import command
from alembic.config import Config

from app.core.auth import configured_api_token
from app.core.config import settings
from app.core.credential_store import credential_cipher


def prepare() -> None:
    # Fail before creating state, and never invent or rotate encryption keys.
    configured_api_token(settings)
    credential_cipher(settings)
    home = Path.home()
    for directory in (settings.workspace_root, home, home / ".ssh",
                      home / ".ssh" / "range42", home / ".ansible"):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
    root = Path(__file__).resolve().parent
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, "head")


def main() -> None:
    os.umask(0o077)
    try:
        prepare()
    except (OSError, RuntimeError) as exc:
        print(f"Container startup failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    argv = sys.argv[1:] or ["uvicorn", "app.main:app", "--host", "0.0.0.0",
                            "--port", "8000", "--workers", "1"]
    os.execvp(argv[0], argv)


if __name__ == "__main__":
    main()
