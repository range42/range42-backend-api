"""/v1/deployments/:id/preflight — create + latest."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session_factory
from app.core.errors import Range42Error
from app.core.models import Deployment, PreflightRecord, ProxmoxHost
from app.core.preflight import (
    PreflightReport,
    check_proxmox_api_status,
    check_resource_budget,
    check_secret_completeness,
    check_vmids,
)
from app.schemas.v1.deployments import PreflightResponse

router = APIRouter()


async def _session() -> AsyncSession:
    async with get_session_factory()() as session:
        yield session


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
    # Placeholder VMID set; real topology resolution happens once compose is
    # wired into the deploy trigger (Plan B final task 52). Preflight structure
    # is stable.
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
