"""Pinned repositories can contain concrete scenarios without topology.json."""
import subprocess

import pytest

from app.core import project
from app.core.errors import ProjectCheckoutError


def make_repository(path, files):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    for name, content in files.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content)
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run([
        "git", "-c", "user.name=Test", "-c", "user.email=test@example.test",
        "commit", "-qm", "Scenario",
    ], cwd=path, check=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path, text=True).strip()


def test_checkout_repository_returns_root_without_requiring_topology(tmp_path):
    src = tmp_path / "source"
    sha = make_repository(src, {"scenarios/content/main.yml": "- hosts: localhost\n"})
    checkout = getattr(project, "checkout_repository", None)
    assert callable(checkout), "Concrete scenarios need a repository checkout independent of topology.json"
    root = checkout(repo_url=src.as_uri(), sha=sha, dest=tmp_path / "project", token=None)
    assert root == tmp_path / "project"
    assert (root / "scenarios/content/main.yml").read_text() == "- hosts: localhost\n"
    assert not (root / "topology.json").exists()


@pytest.mark.parametrize("revision", ["main", "HEAD", "abc123", "--upload-pack=bad"])
def test_checkout_repository_requires_full_commit_sha(tmp_path, revision):
    checkout = getattr(project, "checkout_repository", None)
    assert callable(checkout)
    with pytest.raises(ProjectCheckoutError, match="commit SHA"):
        checkout(repo_url="file:///unused", sha=revision, dest=tmp_path / "project", token=None)
    assert not (tmp_path / "project").exists()


def test_checkout_repository_restores_pinned_content_on_reuse(tmp_path):
    src = tmp_path / "source"
    sha = make_repository(src, {"main.yml": "original"})
    checkout = getattr(project, "checkout_repository", None)
    assert callable(checkout)
    root = checkout(repo_url=src.as_uri(), sha=sha, dest=tmp_path / "project", token=None)
    (root / "main.yml").write_text("changed")
    (root / "injected.yml").write_text("untracked")
    checkout(repo_url=src.as_uri(), sha=sha, dest=root, token=None)
    assert (root / "main.yml").read_text() == "original"
    assert not (root / "injected.yml").exists()


def test_checkout_repository_removes_ignored_generated_files_on_reuse(tmp_path):
    src = tmp_path / "source"
    sha = make_repository(src, {".gitignore": "generated/\n", "main.yml": "pinned"})
    root = project.checkout_repository(repo_url=src.as_uri(), sha=sha, dest=tmp_path / "project", token=None)
    generated = root / "generated"
    generated.mkdir()
    (generated / "tasks.yml").write_text("content outside the pinned revision")

    project.checkout_repository(repo_url=src.as_uri(), sha=sha, dest=root, token=None)

    assert not generated.exists(), "Ignored files must not survive a clean pinned checkout"
