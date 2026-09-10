"""Read-only runtime observations and guarded desired-state operation requests."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import tempfile

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Range42Error
from app.core.models import Deployment, ProxmoxHost
from app.core.runtime_operations import blocked, operation_profile
from app.core.runtime_state import read_runtime_state
from app.core.scenario import prepare_project_scenario
from app.routes.v1.deployments.attempts import _session, reserve_attempt
from app.schemas.v1.deployments import AttemptCreate, AttemptOut
from app.schemas.v1.runtime import RuntimeOperation

router = APIRouter()


async def _deployment(session, deployment_id):
    deployment = await session.get(Deployment, deployment_id)
    if deployment is None:
        raise Range42Error(status=404, code="NOT_FOUND", error="not_found", message="Deployment not found")
    if not deployment.project_sha or deployment.scenario_label == "_universal":
        raise blocked("Runtime controls require a pinned concrete deployment")
    return deployment


@router.get("/{deployment_id}/runtime")
async def runtime_status(deployment_id: str, session: AsyncSession = Depends(_session)):
    deployment = await _deployment(session, deployment_id)
    host = await session.get(ProxmoxHost, deployment.target_host_id)
    if host is None:
        raise blocked("The deployment's target host is unavailable")
    directory = Path(deployment.workspace_path) / "runner"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="runtime-status-", dir=directory) as temporary:
        scenario = await prepare_project_scenario(session, deployment, dest=Path(temporary) / "checkout", scope="runtime")
        state = await read_runtime_state(scenario.playbook.parent, host, deployment_id=deployment_id)
    state.update(project_sha=deployment.project_sha, observed_at=datetime.now(timezone.utc).isoformat())
    state["runtime"] = {"available": False, "operations": []}
    try:
        profile = await asyncio.to_thread(operation_profile, "vm_firewall")
        state["runtime"] = {"available": True, **profile}
    except Range42Error as exc:
        state["runtime"]["reason"] = exc.message
    return state


@router.post("/{deployment_id}/operations", response_model=AttemptOut, status_code=201)
async def create_operation(deployment_id: str, payload: RuntimeOperation,
                           session: AsyncSession = Depends(_session)):
    deployment = await _deployment(session, deployment_id)
    profile = await asyncio.to_thread(operation_profile, payload.kind)
    operation = {"request": payload.model_dump(), "project_sha": deployment.project_sha,
                 "target_host_id": deployment.target_host_id, "runtime": profile}
    return await reserve_attempt(deployment_id, AttemptCreate(scope="configure"), session, operation=operation)
