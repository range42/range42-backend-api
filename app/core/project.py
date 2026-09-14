"""Project repo checkout for build-from-scratch deployments.

Clones the project repo at the pinned project_sha into the workspace.
Shallow clone (depth=1) — full history not needed at deploy time.
Idempotent: same SHA → no-op.
"""
from __future__ import annotations
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import yaml

from app.core.errors import ProjectCheckoutError, Range42Error
from app.core.repository_urls import GIT_HTTP_ENV
from app.core.scenario_manifest import validate_vm_manifest
from app.core.scenario_instances import validate_instance_files, validate_vm_inventory


_AUTHED_URL_RE = re.compile(r'https://[^@]+@')


@dataclass(frozen=True)
class ProjectScenario:
    project_root: Path
    playbook: Path
    inventory: Path
    vmids: list[int]
    checkout_credential: str | None = field(default=None, repr=False, compare=False)


def resolve_project_scenario(
    checkout: Path, *, scenario_label: str, subdir: str | None = None,
    scope: str = "full",
) -> ProjectScenario:
    """Resolve the concrete scenario contract inside a pinned project checkout."""
    def invalid(message: str) -> Range42Error:
        return Range42Error(
            code="PROJECT_SCENARIO_INVALID", error="project_scenario_invalid",
            message=message,
        )

    checkout = checkout.resolve()
    project_subdir = Path(subdir or ".")
    if project_subdir.is_absolute() or ".." in project_subdir.parts:
        raise invalid("project.subdir must stay inside the repository")
    root = (checkout / project_subdir).resolve()
    if not root.is_relative_to(checkout):
        raise invalid("project.subdir resolves outside the repository")
    if not re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", scenario_label):
        raise invalid("scenario_label must name a directory under scenarios/")
    scenario = root / "scenarios" / scenario_label

    def required_file(name: str) -> Path:
        path = (scenario / name).resolve()
        if not path.is_relative_to(root):
            raise invalid(f"Scenario {name} resolves outside the project")
        if not path.is_file():
            raise invalid(f"Scenario requires scenarios/{scenario_label}/{name}")
        return path

    # Runtime operations inspect the pinned scenario, then execute a separate
    # backend-owned wrapper; they never execute its provisioning entrypoint.
    entrypoints = {"full": "main.yml", "configure": "configure.yml", "teardown": "teardown.yml", "runtime": "main.yml"}
    if scope not in entrypoints:
        raise Range42Error(code="PROJECT_SCENARIO_SCOPE_UNSUPPORTED", error="unsupported_scope",
                           message="Concrete scenarios support full, configure and teardown entrypoints")
    playbook = required_file(entrypoints[scope])
    inventory = required_file("hosts.yml")
    manifest_path = required_file("manifest/scenario_vms.json")
    try:
        manifest = validate_vm_manifest(json.loads(manifest_path.read_text()))
        vms = manifest.get("vms") if isinstance(manifest, dict) else None
        if not isinstance(vms, list) or any(
            not isinstance(vm, dict) or type(vm.get("vm_id")) is not int
            or vm["vm_id"] <= 0 for vm in vms
        ):
            raise ValueError("expected vms list with positive integer vm_id values")
    except (OSError, ValueError) as exc:
        raise invalid("Invalid manifest/scenario_vms.json: expected a vms list with integer VMIDs") from exc
    try:
        hosts = yaml.safe_load(inventory.read_text())
        if not isinstance(hosts, dict) or not hosts or any(
            not isinstance(group, dict) for group in hosts.values()
        ):
            raise ValueError("expected an Ansible inventory mapping")
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise invalid("Invalid hosts.yml: expected an Ansible inventory mapping") from exc
    try:
        validate_vm_inventory(manifest, hosts)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise invalid("Invalid hosts.yml: scenario guests and management addresses must match the VM manifest") from None
    try:
        validate_instance_files(scenario, manifest, hosts)
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        raise invalid("Invalid manifest/scenario_instances.json: replication intent, VM/NIC/network identities and inventory must agree") from None
    return ProjectScenario(root, playbook, inventory, [vm["vm_id"] for vm in vms])


def _redact_authed_url(s: str) -> str:
    """Replace 'https://x-access-token:TOKEN@host/...' with 'https://[REDACTED]@host/...'"""
    return _AUTHED_URL_RE.sub('https://[REDACTED]@', s)


def authed_url(url: str, token: str | None) -> str:
    """Embed a Git PAT into an https clone URL as x-access-token.

    Returns the URL unchanged when there is no token or it is not an https URL.
    Callers MUST redact any error containing the result via ``_redact_authed_url``.
    """
    if token and url.startswith("https://"):
        return url.replace("https://", f"https://x-access-token:{quote(token, safe='')}@", 1)
    return url


def _run_git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Git stderr is untrusted and may echo credentials outside an auth URL."""
    try:
        return subprocess.run(
            ["git", *args],
            env={**os.environ, **GIT_HTTP_ENV},
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        raise ProjectCheckoutError(
            message="Git repository operation failed.",
            details=[{"returncode": str(e.returncode)}],
        ) from None
    except FileNotFoundError:
        raise ProjectCheckoutError(message="git not installed") from None


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
    # Compatibility wrapper for the retiring topology-driven caller.
    # Concrete scenarios use checkout_repository directly.
    current = _current_sha(dest)
    if current == sha:
        return dest / "topology.json"

    checkout_repository(repo_url=repo_url, sha=sha, dest=dest, token=token)
    topology = dest / "topology.json"
    if not topology.is_file():
        raise ProjectCheckoutError(
            message=f"topology.json not found in project repo at {sha}",
            details=[{"checked_out_sha": sha}],
        )
    return topology


def checkout_repository(
    *, repo_url: str, sha: str, dest: Path, token: str | None,
) -> Path:
    """Return a clean repository root pinned to a full commit SHA.

    Callers own the destination; never reuse an active attempt's checkout.
    Credentials are used only for the fetch and are not saved as a remote.
    """
    if not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", sha):
        raise ProjectCheckoutError(message="project_sha must be a full commit SHA")
    sha = sha.lower()
    dest = dest.resolve()
    current = _current_sha(dest)

    # Build URL with token if present (stripped from logs by existing redaction)
    url = authed_url(repo_url, token)

    if dest.exists() and (dest / ".git").exists():
        # Existing repo, fetch the SHA and reset
        if current != sha:
            _run_git("fetch", "--depth", "1", url, sha, cwd=dest)
        _run_git("reset", "--hard", sha, cwd=dest)
        _run_git("clean", "-ffdx", cwd=dest)
    else:
        # Clean any existing non-git contents before init
        if dest.exists():
            import shutil
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Initial clone — fetch a single SHA depth-1. Fetch the URL ad-hoc
        # rather than `git remote add origin <url>`: a named remote would
        # persist the authed URL (Git PAT) in .git/config on the workspace disk.
        _run_git("init", "-q", str(dest))
        _run_git("fetch", "--depth", "1", url, sha, cwd=dest)
        _run_git("checkout", "FETCH_HEAD", cwd=dest)

    if _current_sha(dest) != sha:
        raise ProjectCheckoutError(
            message="Checked-out commit does not match project_sha",
        )
    return dest
