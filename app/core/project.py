"""Project repo checkout for build-from-scratch deployments.

Clones the project repo at the pinned project_sha into the workspace.
Shallow clone (depth=1) — full history not needed at deploy time.
Idempotent: same SHA → no-op.
"""
from __future__ import annotations
import subprocess
from pathlib import Path

from app.core.errors import ProjectCheckoutError


def _run_git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run a git command, raising ProjectCheckoutError with stderr on failure."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        raise ProjectCheckoutError(
            message=f"git {' '.join(args)} failed: {e.stderr.strip()}",
            details=[{"stderr": e.stderr, "returncode": str(e.returncode)}],
        ) from e
    except FileNotFoundError as e:
        raise ProjectCheckoutError(message="git not installed") from e


def _current_sha(repo: Path) -> str | None:
    if not (repo / ".git").exists():
        return None
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo, check=True, capture_output=True, text=True,
        )
        return out.stdout.strip()
    except subprocess.CalledProcessError:
        return None


def checkout_project(
    *, repo_url: str, sha: str, dest: Path, token: str | None,
) -> Path:
    """
    Check out a project repo at the given SHA into `dest`.

    - Idempotent: if dest already at sha, returns immediately.
    - Shallow: --depth 1 fetch.
    - Auth: if `token` is provided, embeds via x-access-token URL prefix.
    - Returns: absolute path to dest/topology.json.
    - Raises: ProjectCheckoutError on any failure.
    """
    dest = dest.resolve()
    current = _current_sha(dest)
    if current == sha:
        return dest / "topology.json"

    # Build URL with token if present (stripped from logs by existing redaction)
    url = repo_url
    if token and url.startswith("https://"):
        url = url.replace("https://", f"https://x-access-token:{token}@", 1)

    if dest.exists() and (dest / ".git").exists():
        # Existing repo, fetch the SHA and reset
        _run_git("fetch", "--depth", "1", url, sha, cwd=dest)
        _run_git("reset", "--hard", sha, cwd=dest)
    else:
        # Clean any existing non-git contents before init
        if dest.exists():
            import shutil
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Initial clone — fetch a single SHA depth-1
        _run_git("init", "-q", str(dest))
        _run_git("remote", "add", "origin", url, cwd=dest)
        _run_git("fetch", "--depth", "1", "origin", sha, cwd=dest)
        _run_git("checkout", "FETCH_HEAD", cwd=dest)

    topology = dest / "topology.json"
    if not topology.is_file():
        raise ProjectCheckoutError(
            message=f"topology.json not found in project repo at {sha}",
            details=[{"checked_out_sha": sha}],
        )
    return topology
