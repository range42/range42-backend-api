
from pathlib import Path

_VAULT_PASS_PATH: Path | None = None

def set_vault_path(p: Path | None) -> None:
    global _VAULT_PASS_PATH
    _VAULT_PASS_PATH = p

def get_vault_path() -> Path | None:
    return _VAULT_PASS_PATH

