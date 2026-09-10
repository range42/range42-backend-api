"""Load concrete scenarios from deployment-pinned project repositories."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ProjectCheckoutError, Range42Error
from app.core.models import Deployment, Project, Source
from app.core.project import ProjectScenario, checkout_repository, resolve_project_scenario
from app.core.repository_urls import require_repository_url


def validate_concrete_scope(deployment: Deployment, scope: str) -> None:
    if deployment.scenario_label == "_universal":
        raise Range42Error(
            error="scenario_retired", code="SCENARIO_RETIRED", status=400,
            message="_universal is retired. Save a concrete scenario and create a new deployment from its commit.",
        )
    if deployment.project_sha and scope not in (
        "full", "configure", "teardown", "runtime",
    ):
        raise Range42Error(
            error="scenario_scope_unsupported", code="PROJECT_SCENARIO_SCOPE_UNSUPPORTED", status=400,
            message="This operation has no concrete scenario runner implementation. "
                    "Use full, configure or teardown with an explicit pinned playbook.",
        )


def validate_project_revision(deployment: Deployment, scope: str, project_sha: str | None) -> None:
    if project_sha is not None and (scope != "configure" or not deployment.project_sha
                                    or deployment.scenario_label == "_universal"):
        raise Range42Error(
            code="PROJECT_SCENARIO_REVISION_UNSUPPORTED", error="unsupported_revision",
            message="A new project revision is supported only for configure on a pinned concrete deployment",
        )


def _configuration_targets(scenario: ProjectScenario) -> tuple[str, str | None, str]:
    """Canonical target files prevent a content revision from retargeting hosts."""
    def manifest(name: str, *, optional: bool = False):
        path = scenario.playbook.parent / "manifest" / name
        if optional and not path.exists() and not path.is_symlink():
            return None
        if not path.resolve().is_relative_to(scenario.project_root):
            raise ValueError("manifest outside project")
        return json.dumps(json.loads(path.read_text()), sort_keys=True, separators=(",", ":"))

    try:
        return (manifest("scenario_vms.json"), manifest("scenario_networks.json", optional=True),
                yaml.safe_dump(yaml.safe_load(scenario.inventory.read_text()), sort_keys=True))
    except (OSError, ValueError, yaml.YAMLError):
        raise Range42Error(code="PROJECT_SCENARIO_INVALID", error="project_scenario_invalid",
                           message="Cannot compare the scenario inventory and target manifests") from None


async def prepare_project_scenario(
    session: AsyncSession, deployment: Deployment, *, dest: Path,
    scope: str = "full", project_sha: str | None = None,
) -> ProjectScenario:
    """Use an isolated destination so preflight cannot change a running attempt."""
    validate_project_revision(deployment, scope, project_sha)
    project = await session.get(Project, deployment.project_id)
    if project is None or not project.repo_owner or not project.repo_name:
        raise ProjectCheckoutError(message="Project requires repo_owner and repo_name for scenario checkout")
    source = await session.get(Source, project.source_id)
    if source is None:
        raise ProjectCheckoutError(message="Project source is unavailable")
    for value, nested in ((project.repo_owner, True), (project.repo_name, False)):
        pattern = r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*" if nested else r"[A-Za-z0-9_.-]+"
        if not re.fullmatch(pattern, value) or any(part in (".", "..") for part in value.split("/")):
            raise ProjectCheckoutError(message="Project repository owner or name is invalid")
    repo_url = f"{source.base_url.rstrip('/')}/{project.repo_owner}/{project.repo_name}.git"
    require_repository_url(repo_url)
    effective_sha = project_sha or deployment.project_sha or ""
    token = source.token_ref if source.auth_kind == "pat" else None
    root = await asyncio.to_thread(
        checkout_repository, repo_url=repo_url, sha=effective_sha, dest=dest, token=token,
    )
    scenario = resolve_project_scenario(
        root, subdir=project.subdir, scenario_label=deployment.scenario_label, scope=scope,
    )
    if scope in {"full", "configure"}:
        from app.core.bundle_attachments import validate_scenario_bundles
        await asyncio.to_thread(validate_scenario_bundles, scenario.playbook.parent)
    if project_sha and project_sha.lower() != (deployment.project_sha or "").lower():
        baseline_root = await asyncio.to_thread(
            checkout_repository, repo_url=repo_url, sha=deployment.project_sha,
            dest=dest.with_name(f"{dest.name}-baseline"), token=token,
        )
        baseline = resolve_project_scenario(
            baseline_root, subdir=project.subdir, scenario_label=deployment.scenario_label,
        )
        if _configuration_targets(scenario) != _configuration_targets(baseline):
            raise Range42Error(
                code="PROJECT_CONFIGURATION_TOPOLOGY_CHANGED", error="configuration_topology_changed",
                message="Configure revisions must preserve hosts.yml and the VM/network manifests of the original deployment",
            )
    return scenario
