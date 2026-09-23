"""Create and retrieve deployment readiness reports.

Concrete projects validate the exact scope and commit that the runner executes,
then check declared VM ownership/resources and SDN or existing-bridge networks.
Each preflight uses a disposable checkout. Older installed scenarios retain their
compatibility checks while the concrete project path replaces them.
"""
from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session_factory
from app.core.errors import Range42Error
from app.core.models import (
    Deployment,
    PreflightRecord,
    ProxmoxHost,
)
from app.core.preflight import (
    PreflightCheck,
    PreflightReport,
    check_proxmox_api_status,
    check_resource_budget,
    check_scenario_playbook,
    check_secret_completeness,
    check_vmids,
)
from app.core.scenario import prepare_project_scenario
from app.core.scenario_networks import check_scenario_networks
from app.core.scenario_resources import check_scenario_resources
from app.schemas.v1.deployments import PreflightRequest, PreflightResponse

router = APIRouter()


async def _session() -> AsyncSession:
    async with get_session_factory()() as session:
        yield session


@router.post("/{deployment_id}/preflight", response_model=PreflightResponse)
async def run_preflight(deployment_id: str,
                        payload: PreflightRequest | None = None,
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
    scope = payload.scope if payload else "full"

    if dep.scenario_label == "_universal":
        report.checks.append(PreflightCheck(
            check="scenario_retired", result="block", code="SCENARIO_RETIRED",
            detail="_universal is retired. Save a concrete scenario and create a deployment from its commit.",
        ))
    elif dep.project_sha:
        # Read precisely the project commit the attempt will execute. Separate
        # checkouts avoid changing files beneath a running Ansible process.
        checkout_dir = Path(dep.workspace_path) / "preflight" / uuid.uuid4().hex
        try:
            scenario = await prepare_project_scenario(session, dep, dest=checkout_dir, scope=scope,
                                                       project_sha=payload.project_sha if payload else None)
            report.checks.append(PreflightCheck(check="project_scenario", result="pass"))
            report.checks.append(PreflightCheck(check="scenario_scope", result="pass", detail=scope))
            overrides = json.loads(host.protected_vmids_override_json) if (
                host and host.protected_vmids_override_json
            ) else None
            report.checks.append(check_vmids(scenario.vmids, host_overrides=overrides))
            if scenario.native:
                report.checks.append(PreflightCheck(check="native_context", result="pass",
                    detail=f"Existing Range42 context: {scenario.context.label}"))
                report.checks.append(PreflightCheck(check="native_workflow", result="warn",
                    detail="This executes the complete saved native workflow, including its template, network and custom stages. "
                           "Declared VM IDs are not a complete list of affected resources. Generated-scenario capacity and ownership checks do not apply."))
            else:
                report.checks.extend(await check_scenario_networks(scenario.playbook.parent, host, scope=scope))
                report.checks.extend(await check_scenario_resources(scenario.playbook.parent, host,
                                                                   deployment_id=dep.id, scope=scope))
        except Range42Error as exc:
            report.checks.append(PreflightCheck(
                check="project_scenario", result="block", code=exc.code,
                detail=exc.message, field_path="project_sha",
            ))
        except (ValueError, OSError):
            report.checks.append(PreflightCheck(
                check="project_scenario", result="block", code="PROJECT_SCENARIO_INVALID",
                detail="Cannot read scenario files or target VMID protection configuration",
            ))
        finally:
            shutil.rmtree(checkout_dir, ignore_errors=True)
    else:
        report.checks.append(check_scenario_playbook(dep.scenario_label))
        # Legacy scenarios: keep original placeholder-based checks.
        # Installed legacy scenarios use their pre-rendered inventory.
        report.checks.append(check_vmids([], host_overrides=None))
        report.checks.append(check_resource_budget(
            total_ram_mb_required=0, host_total_ram_mb=1))
        report.checks.append(check_secret_completeness([], provided={}))

    if host and dep.scenario_label != "_universal":
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
