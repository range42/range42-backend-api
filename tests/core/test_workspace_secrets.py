"""The durability contract, tested directly rather than through the route.

`vault_seed` exists to enforce one rule: the vault password is durable if and
only if the deployment row is. These tests own that rule; the route tests in
tests/routes/test_deployment_vault_seed.py check it is actually wired in.
"""
import stat

import pytest

from app.core.workspace_secrets import (
    VaultSeedError,
    vault_pass_path,
    vault_seed,
)


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "secrets").mkdir()
    return tmp_path


def test_writes_the_secret_and_yields_the_path(workspace):
    with vault_seed(workspace, {"vault_password": "pw"}) as path:
        assert path == vault_pass_path(workspace)
        assert path.read_text() == "pw"
    assert vault_pass_path(workspace).read_text() == "pw"


def test_file_is_not_world_readable(workspace):
    with vault_seed(workspace, {"vault_password": "pw"}):
        pass
    mode = stat.S_IMODE(vault_pass_path(workspace).stat().st_mode)
    assert mode == 0o600, oct(mode)


@pytest.mark.parametrize("secrets", [None, {}, {"vault_password": ""},
                                     {"proxmox_token": "t"}])
def test_no_password_is_a_no_op(workspace, secrets):
    """A create that omits the field must not erase an operator-seeded file."""
    vault_pass_path(workspace).write_text("OPERATOR")
    with vault_seed(workspace, secrets) as path:
        assert path is None
    assert vault_pass_path(workspace).read_text() == "OPERATOR"


def test_block_failure_removes_a_file_that_did_not_exist(workspace):
    with pytest.raises(RuntimeError):
        with vault_seed(workspace, {"vault_password": "pw"}):
            raise RuntimeError("commit failed")
    assert not vault_pass_path(workspace).exists(), (
        "a secret outlived the deployment it belonged to")


def test_block_failure_restores_the_previous_secret(workspace):
    vault_pass_path(workspace).write_text("OPERATOR")
    with pytest.raises(RuntimeError):
        with vault_seed(workspace, {"vault_password": "pw"}):
            raise RuntimeError("commit failed")
    assert vault_pass_path(workspace).read_text() == "OPERATOR"


def test_reverts_on_base_exception_too(workspace):
    """A cancelled request must not leave the secret behind either."""
    with pytest.raises(KeyboardInterrupt):
        with vault_seed(workspace, {"vault_password": "pw"}):
            raise KeyboardInterrupt
    assert not vault_pass_path(workspace).exists()


def test_write_failure_raises_vault_seed_error(workspace, monkeypatch):
    import pathlib

    def _boom(self, *a, **kw):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(pathlib.Path, "write_text", _boom)
    with pytest.raises(VaultSeedError) as exc:
        with vault_seed(workspace, {"vault_password": "pw"}):
            pytest.fail("body must not run")
    assert "No space left" in exc.value.reason


def test_revert_failure_does_not_mask_the_original_error(workspace, monkeypatch):
    """Reverting is best-effort — the caller's failure is the important one."""
    import pathlib

    with pytest.raises(RuntimeError, match="the real failure"):
        with vault_seed(workspace, {"vault_password": "pw"}):
            monkeypatch.setattr(
                pathlib.Path, "unlink",
                lambda self, **kw: (_ for _ in ()).throw(OSError("read-only fs")))
            raise RuntimeError("the real failure")
