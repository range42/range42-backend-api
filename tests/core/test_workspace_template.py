import stat

import pytest

from app.core.workspace import Workspace, WorkspaceError


@pytest.fixture
def template(tmp_path, monkeypatch):
    root = tmp_path / "template"
    for directory in (
        root,
        root / "secrets",
        root / "ssh_keys",
        root / "ssh_keys/backend_keys",
        root / "ssh_keys/jump_keys",
    ):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
    for name, value in {
        "secrets/default_vault.yml": "encrypted-vault-fixture",
        "secrets/vault_pass.txt": "private-test-password",
        "ssh_keys/backend_keys/deployer": "private-test-key",
        "ssh_keys/backend_keys/deployer.pub": "public-test-key",
        "ssh_keys/jump_keys/root": "private-test-jump-key",
        "ssh_keys/known_hosts": "pve01 public-test-host-key",
    }.items():
        path = root / name
        path.write_text(value)
        path.chmod(0o600)
    monkeypatch.setenv("RANGE42_WORKSPACE_TEMPLATE_DIR", str(root))
    return root


def create(tmp_path, **kwargs):
    return Workspace.create(
        codename="LAB",
        scenario_label="demo",
        workspace_root=tmp_path / "workspaces",
        **kwargs
    )


def test_new_workspace_inherits_selected_credentials_with_private_modes(
    tmp_path, template
):
    (template / "not-a-credential.txt").write_text("do-not-inherit")
    ws = create(tmp_path)
    assert (ws.path / "secrets/vault_pass.txt").read_text() == "private-test-password"
    assert (
        ws.path / "ssh_keys/backend_keys/deployer"
    ).read_text() == "private-test-key"
    assert not (ws.path / "not-a-credential.txt").exists()
    assert stat.S_IMODE(ws.path.stat().st_mode) == 0o700
    for path in (ws.path / "secrets").rglob("*"):
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600)
    for path in (ws.path / "ssh_keys").rglob("*"):
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600)


def test_missing_configured_template_fails_without_partial_workspace(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("RANGE42_WORKSPACE_TEMPLATE_DIR", str(tmp_path / "missing"))
    with pytest.raises(WorkspaceError) as exc:
        create(tmp_path)
    assert exc.value.code == "WORKSPACE_TEMPLATE_INVALID"
    assert not (tmp_path / "workspaces/LAB-demo").exists()


def test_explicit_user_credentials_skip_default_template(tmp_path, template):
    ws = create(tmp_path, inherit_template=False)
    assert list((ws.path / "secrets").iterdir()) == []
    assert list((ws.path / "ssh_keys").iterdir()) == []


def test_existing_credentials_are_preserved_as_a_coherent_set(tmp_path, template):
    secrets = tmp_path / "workspaces/LAB-demo/secrets"
    secrets.mkdir(parents=True)
    (secrets / "vault_pass.txt").write_text("operator-choice")
    ws = create(tmp_path)
    assert (secrets / "vault_pass.txt").read_text() == "operator-choice"
    assert not (secrets / "default_vault.yml").exists()
    assert list((ws.path / "ssh_keys").iterdir()) == []


@pytest.mark.parametrize(
    "target", ["secrets/default_vault.yml", "ssh_keys/backend_keys/deployer"]
)
def test_template_file_symlinks_are_rejected_without_copying(
    tmp_path, template, target
):
    outside = tmp_path / "outside"
    outside.write_text("outside-secret")
    outside.chmod(0o600)
    path = template / target
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(WorkspaceError) as exc:
        create(tmp_path)
    assert exc.value.code == "WORKSPACE_TEMPLATE_INVALID"
    assert not (tmp_path / "workspaces/LAB-demo").exists()


def test_template_directory_symlinks_are_rejected(tmp_path, template):
    directory = template / "ssh_keys/backend_keys"
    for path in directory.iterdir():
        path.unlink()
    directory.rmdir()
    directory.symlink_to(template / "ssh_keys/jump_keys", target_is_directory=True)
    with pytest.raises(WorkspaceError):
        create(tmp_path)
    assert not (tmp_path / "workspaces/LAB-demo").exists()


@pytest.mark.parametrize(
    "target,mode", [("secrets/vault_pass.txt", 0o644), ("ssh_keys/backend_keys", 0o755)]
)
def test_unsafe_template_permissions_are_rejected(tmp_path, template, target, mode):
    (template / target).chmod(mode)
    with pytest.raises(WorkspaceError):
        create(tmp_path)
    assert not (tmp_path / "workspaces/LAB-demo").exists()


def test_destination_symlink_cannot_write_outside_workspace(tmp_path, template):
    target = tmp_path / "workspaces/LAB-demo"
    target.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (target / "secrets").symlink_to(outside, target_is_directory=True)
    with pytest.raises(WorkspaceError):
        create(tmp_path)
    assert list(outside.iterdir()) == []


def test_copy_failure_removes_only_new_files_and_directories(
    tmp_path, template, monkeypatch
):
    def fail(fd, data):
        raise OSError("simulated disk failure")

    monkeypatch.setattr("app.core.workspace.os.write", fail)
    with pytest.raises(WorkspaceError):
        create(tmp_path)
    assert not (tmp_path / "workspaces/LAB-demo").exists()
    assert (template / "secrets/vault_pass.txt").read_text() == "private-test-password"
