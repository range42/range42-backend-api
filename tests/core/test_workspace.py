import pytest
from app.core.workspace import (
    Workspace, WorkspaceError, detect_fs_type, is_local_fs, shred_envvars,
)


def test_detect_fs_type_returns_string(tmp_path):
    fs = detect_fs_type(tmp_path)
    assert isinstance(fs, str) and len(fs) > 0


def test_is_local_fs_accepts_common_local(monkeypatch):
    for name in ("ext4", "xfs", "btrfs", "tmpfs", "zfs"):
        assert is_local_fs(name) is True


def test_is_local_fs_rejects_network(monkeypatch):
    for name in ("nfs", "nfs4", "cifs", "fuse.sshfs", "fuse"):
        assert is_local_fs(name) is False


def test_workspace_bootstrap_creates_layout(tmp_path):
    ws = Workspace.create(codename="ALPHA", scenario_label="demo",
                          workspace_root=tmp_path)
    for sub in ("inventory", "secrets", "ssh_keys", "bin", "runner"):
        assert (ws.path / sub).is_dir()
    assert ws.path == tmp_path / "ALPHA-demo"


def test_workspace_refuses_non_local_fs(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.workspace.detect_fs_type", lambda p: "nfs4")
    with pytest.raises(WorkspaceError) as ei:
        Workspace.create(codename="X", scenario_label="y", workspace_root=tmp_path)
    assert ei.value.code == "WORKSPACE_NON_LOCAL_FS"


# ---------------------------------------------------------------------------
# T10: token shred at attempt completion
# ---------------------------------------------------------------------------

def test_shred_envvars_overwrites_then_unlinks(tmp_path):
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    envvars = env_dir / "envvars"
    envvars.write_text("SECRET=xyz\n")
    assert envvars.is_file()

    shred_envvars(envvars)
    assert not envvars.exists()


def test_shred_envvars_no_op_on_missing_file(tmp_path):
    """Shredding a non-existent file is a no-op (idempotent on cleanup)."""
    nonexistent = tmp_path / "missing"
    # Should not raise
    shred_envvars(nonexistent)


def test_shred_envvars_handles_extravars_path(tmp_path):
    """Helper signature accepts arbitrary file paths, not just env/envvars by name."""
    f = tmp_path / "env" / "extravars"
    f.parent.mkdir(parents=True)
    f.write_text('{"r42_topology_path": "/tmp/x"}')
    shred_envvars(f)
    assert not f.exists()
