"""/v1/deployments/:id/preflight — create + latest.

For ``_universal`` scenarios this route additionally:

1. Clones the pinned project_sha into ``<workspace>/project`` via
   ``checkout_project()`` (idempotent — same SHA is a no-op).
2. Reads ``topology.json`` from the cloned dir.
3. Runs the topology-aware checks introduced in B-T6/T7/T8:
   ``check_topology_assets``, ``check_vmid_safety_for_topology``,
   ``check_topology_node_role``.
4. Honors any ``topology.preflight_checks[]`` array via the declarative
   dispatcher (B-T10).

Legacy scenarios (``demo_lab``, ``blank_*``, etc.) keep the original
placeholder-based behavior unchanged.

Spec: ``2026-05-07-build-from-scratch-design.md`` §5.5.5.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session_factory
from app.core.errors import ProjectCheckoutError, Range42Error
from app.core.models import (
    Deployment,
    PreflightRecord,
    Project,
    ProxmoxHost,
    Source,
)
from app.core.preflight import (
    PreflightCheck,
    PreflightReport,
    check_proxmox_api_status,
    check_resource_budget,
    check_secret_completeness,
    check_topology_assets,
    check_topology_node_role,
    check_vmid_safety_for_topology,
    check_vmids,
    run_declarative_checks,
)
from app.core.project import checkout_project
from app.schemas.v1.deployments import PreflightResponse

router = APIRouter()


async def _session() -> AsyncSession:
    async with get_session_factory()() as session:
        yield session


async def _run_universal_topology_checks(
    session: AsyncSession,
    dep: Deployment,
    host: ProxmoxHost | None,
    report: PreflightReport,
) -> None:
    """Load the project's topology.json and append topology-aware checks.

    Mirrors the ``_universal`` clone+load pattern in
    ``app/core/deploy_trigger.py``. Boundary errors (missing required
    Project fields, git failures, malformed JSON) are surfaced as
    ``topology_load`` block checks so the route returns a structured
    report rather than a 5xx.
    """
    project = (await session.execute(
        select(Project).where(Project.id == dep.project_id))
    ).scalar_one_or_none()
    if project is None:
        report.checks.append(PreflightCheck(
            check="topology_load",
            result="block",
            code="PROJECT_FIELDS_MISSING",
            detail=f"Project {dep.project_id} not found",
        ))
        return

    missing: list[str] = []
    if not dep.project_sha:
        missing.append("deployment.project_sha")
    if not project.repo_owner:
        missing.append("project.repo_owner")
    if not project.repo_name:
        missing.append("project.repo_name")
    if missing:
        report.checks.append(PreflightCheck(
            check="topology_load",
            result="block",
            code="PROJECT_FIELDS_MISSING",
            detail=f"required fields missing: {', '.join(missing)}",
        ))
        return

    source = (await session.execute(
        select(Source).where(Source.id == project.source_id))
    ).scalar_one_or_none()
    if source is None:
        report.checks.append(PreflightCheck(
            check="topology_load",
            result="block",
            code="SOURCE_NOT_FOUND",
            detail=f"Source {project.source_id} not found",
        ))
        return

    repo_url = (
        f"{source.base_url.rstrip('/')}/"
        f"{project.repo_owner}/{project.repo_name}.git"
    )
    project_dir = Path(dep.workspace_path) / "project"

    try:
        topology_path = checkout_project(
            repo_url=repo_url,
            sha=dep.project_sha,
            dest=project_dir,
            token=source.token_ref,
        )
    except ProjectCheckoutError as e:
        report.checks.append(PreflightCheck(
            check="topology_load",
            result="block",
            code="PROJECT_CHECKOUT_FAILED",
            detail=str(e),
        ))
        return

    try:
        topology = json.loads(topology_path.read_text())
    except (OSError, ValueError) as e:
        report.checks.append(PreflightCheck(
            check="topology_load",
            result="block",
            code="TOPOLOGY_PARSE_FAILED",
            detail=str(e),
        ))
        return

    # Build the registered_source_base_urls set from all known Sources so
    # external_git attachments can be validated.
    sources = (await session.execute(select(Source))).scalars().all()
    registered = {s.base_url for s in sources if s.base_url}

    # host_overrides comes from the target ProxmoxHost.protected_vmids_override_json
    # (Text column with JSON-encoded list[list[int]]); None if not set.
    host_overrides: list[list[int]] | None = None
    if host is not None and host.protected_vmids_override_json:
        try:
            host_overrides = json.loads(host.protected_vmids_override_json)
        except ValueError:
            host_overrides = None

    report.checks.extend(await check_topology_assets(
        project_dir,
        catalog_dir=None,
        topology=topology,
        registered_source_base_urls=registered,
    ))
    report.checks.append(await check_vmid_safety_for_topology(
        topology,
        team_count=dep.team_count or 1,
        host_overrides=host_overrides,
    ))
    report.checks.extend(check_topology_node_role(topology))

    # Declarative preflight_checks[] from the topology — pass a context
    # rich enough to satisfy any check the dispatcher might call.
    declarative_context: dict = {
        "topology": topology,
        "team_count": dep.team_count or 1,
        "host_overrides": host_overrides,
        "project_dir": project_dir,
        "catalog_dir": None,
        "registered_source_base_urls": registered,
    }
    if host is not None:
        declarative_context["api_url"] = host.api_url
        declarative_context["token_ref"] = host.token_ref
    report.checks.extend(await run_declarative_checks(
        topology.get("preflight_checks") or [],
        context=declarative_context,
    ))


@router.post("/{deployment_id}/preflight", response_model=PreflightResponse)
async def run_preflight(deployment_id: str,
                        session: AsyncSession = Depends(_session)):
    dep = (await session.execute(
        select(Deployment).where(Deployment.id == deployment_id))).scalar_one_or_none()
    if dep is None:
        raise Range42Error(
            error="not_found", code="NOT_FOUND", status=404,
            message=f"Deployment {deployment_id} not found",
        )
    host = (await session.execute(
        select(ProxmoxHost).where(ProxmoxHost.id == dep.target_host_id))).scalar_one_or_none()
    report = PreflightReport()

    if dep.scenario_label == "_universal":
        # Universal scenario: load topology and run topology-aware checks.
        await _run_universal_topology_checks(session, dep, host, report)
    else:
        # Legacy scenarios: keep original placeholder-based checks.
        # Real topology resolution is _universal-only; legacy paths use
        # pre-rendered inventory and have no topology.json.
        report.checks.append(check_vmids([], host_overrides=None))
        report.checks.append(check_resource_budget(
            total_ram_mb_required=0, host_total_ram_mb=1))
        report.checks.append(check_secret_completeness([], provided={}))

    if host:
        report.checks.append(
            await check_proxmox_api_status(host.api_url, host.token_ref)
        )
    row = PreflightRecord(
        id=uuid.uuid4().hex[:16],
        deployment_id=deployment_id,
        attempt_id=dep.current_attempt_id,
        ts=datetime.now(timezone.utc),
        result=report.result,
        checks_json=json.dumps([c.__dict__ for c in report.checks]),
    )
    session.add(row)
    await session.commit()
    return PreflightResponse(
        id=row.id,
        deployment_id=row.deployment_id,
        attempt_id=row.attempt_id,
        ts=row.ts,
        result=row.result,
        checks=[c.__dict__ for c in report.checks],
    )


@router.get("/{deployment_id}/preflight", response_model=PreflightResponse)
async def latest_preflight(deployment_id: str,
                           session: AsyncSession = Depends(_session)):
    row = (await session.execute(
        select(PreflightRecord)
        .where(PreflightRecord.deployment_id == deployment_id)
        .order_by(desc(PreflightRecord.ts)).limit(1))).scalar_one_or_none()
    if row is None:
        raise Range42Error(
            error="not_found", code="NOT_FOUND", status=404,
            message=f"No preflight record for {deployment_id}",
        )
    return PreflightResponse(
        id=row.id,
        deployment_id=row.deployment_id,
        attempt_id=row.attempt_id,
        ts=row.ts,
        result=row.result,
        checks=json.loads(row.checks_json),
    )
