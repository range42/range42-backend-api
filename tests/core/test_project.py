import subprocess
from pathlib import Path
import pytest
from app.core.project import checkout_project
from app.core.errors import ProjectCheckoutError


def _make_test_repo(path: Path) -> str:
    """Create a local git repo with a topology.json at HEAD; return the SHA."""
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "topology.json").write_text('{"schema_version": "1.0"}')
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=path, check=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True, check=True,
    ).stdout.strip()
    return sha


def test_checkout_project_clones_at_sha(tmp_path):
    src = tmp_path / "src"
    sha = _make_test_repo(src)
    dst = tmp_path / "ws" / "project"

    result = checkout_project(repo_url=f"file://{src}", sha=sha, dest=dst, token=None)

    assert result == dst / "topology.json"
    assert (dst / "topology.json").read_text() == '{"schema_version": "1.0"}'


def test_checkout_project_idempotent_same_sha(tmp_path):
    src = tmp_path / "src"
    sha = _make_test_repo(src)
    dst = tmp_path / "ws" / "project"

    checkout_project(repo_url=f"file://{src}", sha=sha, dest=dst, token=None)
    mtime1 = (dst / ".git" / "HEAD").stat().st_mtime

    # Second call at same SHA should be a no-op (no re-clone)
    checkout_project(repo_url=f"file://{src}", sha=sha, dest=dst, token=None)
    mtime2 = (dst / ".git" / "HEAD").stat().st_mtime

    assert mtime1 == mtime2, "Re-checkout at same SHA should be idempotent"


def test_checkout_project_fails_typed(tmp_path):
    dst = tmp_path / "ws" / "project"
    with pytest.raises(ProjectCheckoutError) as exc:
        checkout_project(
            repo_url="file:///nonexistent/repo",
            sha="0000000000000000000000000000000000000000",
            dest=dst,
            token=None,
        )
    assert exc.value.code == "PROJECT_CHECKOUT_FAILED"


def test_checkout_project_fails_when_topology_missing(tmp_path):
    """Repo with no topology.json should fail with typed error."""
    src = tmp_path / "src"
    src.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=src, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=src, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=src, check=True)
    (src / "README.md").write_text("no topology here")
    subprocess.run(["git", "add", "."], cwd=src, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=src, check=True)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=src, capture_output=True, text=True, check=True).stdout.strip()
    dst = tmp_path / "ws" / "project"
    with pytest.raises(ProjectCheckoutError) as exc:
        checkout_project(repo_url=f"file://{src}", sha=sha, dest=dst, token=None)
    assert "topology.json" in str(exc.value.message) or "topology.json" in str(exc.value)


def test_run_git_redacts_token_in_error_message(tmp_path):
    """Token in URL must not appear in error messages or details.

    Exercises the existing-repo fetch branch where the authed URL is passed
    as an arg to `git fetch <url> <sha>`. On failure the args are joined
    into the error message — without redaction the raw token would leak.
    """
    fake_token = "ghp_FAKETOKENABCDEFGHIJ12345"
    bad_url = f"https://x-access-token:{fake_token}@github.com/nonexistent/repo.git"

    # Pre-create a .git directory so checkout_project takes the fetch branch
    # (which passes the URL directly to `git fetch <url> <sha>`).
    dst = tmp_path / "ws" / "project"
    dst.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=dst, check=True)

    with pytest.raises(ProjectCheckoutError) as exc:
        checkout_project(
            repo_url=bad_url,
            sha="0000000000000000000000000000000000000001",  # different from current
            dest=dst,
            token=None,  # token already in URL
        )

    err = exc.value
    # Token must not appear anywhere in the error
    assert fake_token not in str(err.message), f"Token leaked in message: {err.message}"
    if err.details:
        for detail in err.details:
            for v in detail.values():
                assert fake_token not in str(v), f"Token leaked in details: {detail}"
    assert "[REDACTED]" in str(err.message) or "git not installed" in str(err.message)
