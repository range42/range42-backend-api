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


def test_is_local_fs_accepts_both_overlay_spellings(monkeypatch):
    """`stat -f -c %T` reports "overlayfs" on some kernels, "overlay" on others.

    Rejecting either makes the backend refuse to create a workspace when its
    root is on the container's own layer instead of a bind mount — which is
    exactly what happens in a containerised CI job.
    """
    for name in ("overlay", "overlayfs"):
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


def test_shred_envvars_actually_overwrites_before_unlink(tmp_path, monkeypatch):
    """Verify zero-fill happens before unlink, not just bare unlink.

    The previous test (``test_shred_envvars_overwrites_then_unlinks``) only
    asserts ``not envvars.exists()`` afterwards — which would also pass for
    a plain ``path.unlink()`` implementation. This tightens the contract:
    the file's bytes at the moment of unlink must be all-NULs, not the
    original secret content.
    """
    from pathlib import Path

    env_dir = tmp_path / "env"
    env_dir.mkdir()
    envvars = env_dir / "envvars"
    secret_content = "SECRET=this_must_be_shredded\n"
    envvars.write_text(secret_content)

    # Capture the file's contents at the moment of unlink, before it disappears
    captured: dict = {}
    real_unlink = Path.unlink

    def _capture_then_unlink(self, *args, **kwargs):
        if self == envvars:
            captured["bytes_at_unlink"] = self.read_bytes()
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _capture_then_unlink)

    shred_envvars(envvars)

    assert not envvars.exists(), "file should be unlinked"
    captured_bytes = captured.get("bytes_at_unlink", b"")
    assert b"SECRET" not in captured_bytes, (
        "shred_envvars failed to overwrite secret content before unlink "
        f"(got: {captured_bytes!r})"
    )
    assert all(b == 0 for b in captured_bytes), (
        f"shred should fill with NULs, got: {captured_bytes!r}"
    )
