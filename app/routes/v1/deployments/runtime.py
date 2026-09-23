"""Read-only runtime observations and guarded desired-state operation requests."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import tempfile

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.errors import Range42Error
from app.core.models import Attempt, Deployment, ProxmoxHost
from app.core.runtime_operations import blocked, operation_profile, target_identity, plan_operation
from app.core.runtime_state import read_runtime_state, read_runtime_report
from app.core.runtime_review import ADMIN_OPERATIONS, REVIEWED_OPERATIONS, authorize_operation, review_fingerprint, verify_review
from app.core.scenario import prepare_project_scenario
from app.routes.v1.deployments.attempts import _session, reserve_attempt
from app.schemas.v1.deployments import AttemptCreate, AttemptOut
from app.schemas.v1.runtime import RuntimeOperation
from app.schemas.v1.runtime_reports import RuntimeReport

router = APIRouter()


async def _deployment(session, deployment_id):
    deployment = await session.get(Deployment, deployment_id)
    if deployment is None:
        raise Range42Error(status=404, code="NOT_FOUND", error="not_found", message="Deployment not found")
    if not deployment.project_sha or deployment.scenario_label == "_universal":
        raise blocked("Runtime controls require a pinned concrete deployment")
    return deployment


@router.get("/{deployment_id}/runtime")
async def runtime_status(deployment_id: str, request: Request, session: AsyncSession = Depends(_session)):
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
    principal = getattr(request.state, "principal", None)
    state["permissions"] = {"admin": bool(principal and principal.role == "admin"),
                            "operate": bool(principal and principal.role in ("admin", "operator"))}
    if not state["permissions"]["admin"]:
        state["runtime"]["operations"] = [kind for kind in state["runtime"]["operations"] if kind not in ADMIN_OPERATIONS]
    return state


@router.get("/{deployment_id}/runtime-report", response_model=RuntimeReport)
async def runtime_report(deployment_id: str, session: AsyncSession = Depends(_session)):
    deployment = await _deployment(session, deployment_id)
    host = await session.get(ProxmoxHost, deployment.target_host_id)
    target_identity(host)
    directory = Path(deployment.workspace_path) / "runner"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="runtime-report-", dir=directory) as temporary:
        scenario = await prepare_project_scenario(session, deployment, dest=Path(temporary) / "checkout", scope="runtime")
        report = await read_runtime_report(scenario.playbook.parent, host, deployment_id=deployment_id)
    report["project_sha"] = deployment.project_sha
    latest = await session.scalar(select(Attempt).where(
        Attempt.deployment_id == deployment_id, Attempt.scope == "runtime",
        Attempt.operation["request"]["kind"].as_string() == "runtime_observe",
    ).order_by(Attempt.started_at.desc(), Attempt.id.desc()).limit(1))
    if (latest and latest.operation.get("project_sha") == deployment.project_sha
            and latest.operation.get("target_host_id") == deployment.target_host_id
            and latest.operation.get("target_identity") == target_identity(host)
            and latest.operation_result and latest.operation_result.get("live_nat")):
        report["live_nat"] = {**latest.operation_result["live_nat"], "attempt_id": latest.id,
                              "reason": "Last native observation; runtime state may have changed since this attempt."}
    return report


async def _current_plan(session, deployment, host, operation):
    directory = Path(deployment.workspace_path) / "runner"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="runtime-plan-", dir=directory) as temporary:
        scenario = await prepare_project_scenario(session, deployment, dest=Path(temporary) / "checkout", scope="runtime")
        if operation["request"]["kind"] in ("firewall_alias", "firewall_rule"):
            from app.core.runtime_firewall import firewall_plan
            return await firewall_plan(scenario.playbook.parent, host, deployment_id=deployment.id, request=operation["request"])
        state = await read_runtime_state(scenario.playbook.parent, host, deployment_id=deployment.id)
        if operation["request"]["kind"] == "sdn_network":
            from app.core.runtime_networks import read_network_lifecycle
            state["network_lifecycle"] = await read_network_lifecycle(scenario.playbook.parent, host, deployment_id=deployment.id)
        return plan_operation(operation["request"], state)


def _operation(payload, deployment, host, profile):
    return {"request": payload.model_dump(), "project_sha": deployment.project_sha,
            "target_host_id": deployment.target_host_id, "target_identity": target_identity(host), "runtime": profile}


@router.post("/{deployment_id}/operations/plan")
async def preview_operation(deployment_id: str, payload: RuntimeOperation, request: Request,
                            session: AsyncSession = Depends(_session)):
    authorize_operation(payload.kind, getattr(request.state, "principal", None))
    deployment = await _deployment(session, deployment_id)
    profile = await asyncio.to_thread(operation_profile, payload.kind)
    host = await session.get(ProxmoxHost, deployment.target_host_id)
    operation = _operation(payload, deployment, host, profile)
    plan = await _current_plan(session, deployment, host, operation)
    return {**operation, "plan": plan, "review_fingerprint": review_fingerprint(operation, plan)}


@router.post("/{deployment_id}/operations", response_model=AttemptOut, status_code=201)
async def create_operation(deployment_id: str, payload: RuntimeOperation, request: Request,
                           session: AsyncSession = Depends(_session)):
    principal = getattr(request.state, "principal", None)
    authorize_operation(payload.kind, principal)
    if payload.kind in REVIEWED_OPERATIONS and not payload.review_fingerprint:
        raise blocked("Review the current target and affected resources before applying", "RUNTIME_REVIEW_REQUIRED")
    deployment = await _deployment(session, deployment_id)
    profile = await asyncio.to_thread(operation_profile, payload.kind)
    host = await session.get(ProxmoxHost, deployment.target_host_id)
    operation = _operation(payload, deployment, host, profile)
    if payload.kind in REVIEWED_OPERATIONS:
        operation.update(authorized_role=principal.role, authorized_actor=principal.actor_id)
        verify_review(operation, await _current_plan(session, deployment, host, operation))
    return await reserve_attempt(deployment_id, AttemptCreate(scope="configure"), session, operation=operation)
