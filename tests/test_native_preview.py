import json

import git
import pytest

from app.core.errors import Range42Error


def repository(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    repo = git.Repo.init(root)
    base = root / "scenarios/lab"
    (base / "manifest").mkdir(parents=True)
    (base / "main.yml").write_text("---\n- hosts: proxmox\n")
    (base / "manifest/scenario_vms.json").write_text(
        json.dumps({"vms": [], "templates": []})
    )
    (base / "secrets").mkdir()
    (base / "secrets/password.txt").write_text("MUST-NOT-RETURN")
    (base / "binary.dat").write_bytes(b"\x00\xff\x02")
    (base / "escape").symlink_to("/etc/passwd")
    repo.index.add(["scenarios"])
    repo.index.commit("fixture")
    return root, repo.head.commit.hexsha


def test_preview_reads_the_exact_git_snapshot_and_excludes_private_paths_and_symlinks(
    tmp_path,
):
    from app.core.native_preview import preview_native_files

    root, sha = repository(tmp_path)
    (root / "scenarios/lab/main.yml").write_text("UNCOMMITTED-CONTENT")
    value = preview_native_files(root, "scenarios/lab", sha)
    assert (
        value["revision"] == sha
        and value["files"]["scenarios/lab/main.yml"] == "---\n- hosts: proxmox\n"
    )
    assert value["files"]["scenarios/lab/binary.dat"]["encoding"] == "base64"
    assert not any("secrets" in path or "escape" in path for path in value["files"])
    assert "MUST-NOT-RETURN" not in json.dumps(
        value
    ) and "UNCOMMITTED" not in json.dumps(value)


@pytest.mark.parametrize(
    "path",
    ["../secrets", "/etc/passwd", "scenarios/lab/../../secret", "scenarios/lab/escape"],
)
def test_preview_refuses_traversal_and_non_scenarios(tmp_path, path):
    from app.core.native_preview import preview_native_files

    root, sha = repository(tmp_path)
    with pytest.raises(Range42Error):
        preview_native_files(root, path, sha)


def test_preview_rejects_an_unreviewed_commit(tmp_path):
    from app.core.native_preview import preview_native_files

    root, sha = repository(tmp_path)
    with pytest.raises(Range42Error):
        preview_native_files(root, "scenarios/lab", "a" * 40)
