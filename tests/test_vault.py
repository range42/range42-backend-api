from pathlib import Path
from app.core.vault import VaultManager


def test_vault_manager_starts_empty():
    vm = VaultManager()
    assert vm.get_vault_path() is None


def test_vault_manager_set_and_get():
    vm = VaultManager()
    p = Path("/tmp/test-vault.txt")
    vm.set_vault_path(p)
    assert vm.get_vault_path() == p


def test_vault_manager_reset():
    vm = VaultManager()
    vm.set_vault_path(Path("/tmp/test-vault.txt"))
    vm.set_vault_path(None)
    assert vm.get_vault_path() is None
