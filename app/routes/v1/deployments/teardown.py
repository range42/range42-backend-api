"""DELETE /v1/deployments/:id — teardown attempt with confirm-phrase gate.

Enqueues a teardown-scoped attempt only after confirm_codename matches
the deployment codename exactly. Refuses teardown when the deployment is
in the 'unknown' terminal state per spec §12.
"""
from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session_factory
from app.core.errors import Range42Error
from app.core.models import Attempt, Deployment
from app.core.vmid_guard import filter_safe_vmids
from app.schemas.v1.deployments import AttemptOut, TeardownRequest

router = APIRouter()


async def _session() -> AsyncSession:
    async with get_session_factory()() as session:
        yield session


@router.delete("/{deployment_id}", response_model=AttemptOut,
               status_code=status.HTTP_202_ACCEPTED)
async def teardown(deployment_id: str,
                   payload: TeardownRequest,
                   session: AsyncSession = Depends(_session)):
    dep = (await session.execute(
        select(Deployment).where(Deployment.id == deployment_id))).scalar_one_or_none()
    if dep is None:
        raise Range42Error(
            error="not_found", code="NOT_FOUND", status=404,
            message=f"Deployment {deployment_id} not found",
        )
    if payload.confirm_codename != dep.codename:
        raise Range42Error(
            error="confirm_mismatch", code="TEARDOWN_CONFIRM_MISMATCH", status=400,
            message="confirm_codename did not match deployment codename",
            details=[{"field": "confirm_codename",
                      "reason": "must equal deployment codename"}],
        )
    if dep.state == "unknown":
        raise Range42Error(
            error="teardown_blocked", code="DEPLOYMENT_UNKNOWN_STATE", status=409,
            message="Deployment in 'unknown' terminal state; human-resolve before teardown",
            details=[{"field": "state",
                      "reason": "unknown blocks teardown per spec §12"}],
        )

    # Protected-VMID safeguard: the runner-side teardown playbook is the
    # canonical enforcer, but we record intent here by partitioning any
    # declared VMIDs on the proxmox_host (via its override list). The API
    # layer does not destroy VMs directly — it enqueues an attempt and
    # lets the detached runner do the work. The safeguard below is a
    # forward-looking hook: once topology is wired into workspace
    # manifests, we can feed the real VMID list to filter_safe_vmids().
    # For now, we do not reject based on VMIDs because the deployment
    # row does not hold them. This matches the cut-list.
    _ = filter_safe_vmids  # retained for later wiring

    # Cleanup workspace dir so re-create with same CODENAME-SCENARIO is clean.
    # The runner-side teardown deletes VMs/LXC/networks via Ansible; this
    # block removes the local workspace directory once the attempt is
    # enqueued. Missing or non-local workspaces are a no-op.
    ws_path = Path(dep.workspace_path)
    if ws_path.exists() and ws_path.is_dir():
        shutil.rmtree(ws_path, ignore_errors=True)

    att = Attempt(
        id=uuid.uuid4().hex[:16],
        deployment_id=deployment_id,
        scope="teardown",
        state="pending",
        started_at=datetime.now(timezone.utc),
    )
    session.add(att)
    dep.current_attempt_id = att.id
    dep.state = "pending"
    await session.commit()
    await session.refresh(att)
    return AttemptOut.model_validate(att, from_attributes=True)
