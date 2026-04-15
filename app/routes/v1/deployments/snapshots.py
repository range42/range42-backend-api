"""Snapshot + rollback + cancel endpoints.

- POST /snapshot — enqueues a scoped snapshot attempt.
- POST /rollback — refuses when required snapshot is expired
  (SNAPSHOT_EXPIRED) or missing (SNAPSHOT_MISSING) per spec §18.6.
- GET /snapshots — lists persisted snapshot rows.
- POST /cancel — signals the running attempt to abort (SIGTERM).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session_factory
from app.core.errors import Range42Error
from app.core.models import Attempt, Deployment, Snapshot
from app.schemas.v1.common import Page
from app.schemas.v1.deployments import (
    AttemptOut,
    RollbackRequest,
    SnapshotCreate,
    SnapshotOut,
)

router = APIRouter()


async def _session() -> AsyncSession:
    async with get_session_factory()() as session:
        yield session


@router.post("/{deployment_id}/snapshot", response_model=AttemptOut,
             status_code=status.HTTP_202_ACCEPTED)
async def snapshot(deployment_id: str, payload: SnapshotCreate,
                   session: AsyncSession = Depends(_session)):
    dep = (await session.execute(
        select(Deployment).where(Deployment.id == deployment_id))).scalar_one_or_none()
    if dep is None:
        raise Range42Error(
            error="not_found", code="NOT_FOUND", status=404,
            message=f"Deployment {deployment_id} not found",
        )
    att = Attempt(
        id=uuid.uuid4().hex[:16],
        deployment_id=deployment_id,
        scope=f"snapshot_{payload.scope}",
        team_id=payload.team_id,
        state="pending",
        started_at=datetime.now(timezone.utc),
    )
    session.add(att)
    await session.commit()
    await session.refresh(att)
    return AttemptOut.model_validate(att, from_attributes=True)


@router.post("/{deployment_id}/rollback", response_model=AttemptOut,
             status_code=status.HTTP_202_ACCEPTED)
async def rollback(deployment_id: str, payload: RollbackRequest,
                   session: AsyncSession = Depends(_session)):
    dep = (await session.execute(
        select(Deployment).where(Deployment.id == deployment_id))).scalar_one_or_none()
    if dep is None:
        raise Range42Error(
            error="not_found", code="NOT_FOUND", status=404,
            message=f"Deployment {deployment_id} not found",
        )
    # Refuse rollback when required snapshot is missing or expired (§18.6).
    q = select(Snapshot).where(Snapshot.deployment_id == deployment_id)
    if payload.scope == "team":
        q = q.where(Snapshot.team_id == payload.team_id)
    snaps = (await session.execute(q)).scalars().all()
    if not snaps:
        raise Range42Error(
            error="no_snapshot", code="SNAPSHOT_MISSING", status=409,
            message="No snapshot available for requested rollback scope",
            details=[{"field": "scope",
                      "reason": f"No snapshots match scope={payload.scope} team_id={payload.team_id}"}],
        )
    expired = [s.id for s in snaps if s.expired]
    if expired:
        raise Range42Error(
            error="snapshot_expired", code="SNAPSHOT_EXPIRED", status=409,
            message="Required snapshot is expired",
            details=[{"field": "snapshot_id",
                      "reason": f"expired snapshots: {','.join(expired)}"}],
        )
    scope = (f"rollback_team_{payload.team_id}"
             if payload.scope == "team" else f"rollback_{payload.scope}")
    att = Attempt(
        id=uuid.uuid4().hex[:16],
        deployment_id=deployment_id,
        scope=scope,
        team_id=payload.team_id,
        state="pending",
        started_at=datetime.now(timezone.utc),
    )
    session.add(att)
    await session.commit()
    await session.refresh(att)
    return AttemptOut.model_validate(att, from_attributes=True)


@router.get("/{deployment_id}/snapshots", response_model=Page[SnapshotOut])
async def list_snapshots(deployment_id: str,
                         session: AsyncSession = Depends(_session)):
    rows = (await session.execute(
        select(Snapshot).where(Snapshot.deployment_id == deployment_id))).scalars().all()
    return Page[SnapshotOut](
        items=[SnapshotOut.model_validate(r, from_attributes=True) for r in rows],
        total=len(rows), offset=0, limit=len(rows),
    )


# Cancel — signals the running attempt to abort. Frontend DeploymentDetail
# wires the "Cancel" button to this. Spec §7 Overview tab: Cancel/Pause.
@router.post("/{deployment_id}/cancel", response_model=AttemptOut,
             status_code=status.HTTP_202_ACCEPTED)
async def cancel_current_attempt(deployment_id: str,
                                 session: AsyncSession = Depends(_session)):
    from app.core.runner_detached import signal_running_attempt

    dep = await session.get(Deployment, deployment_id)
    if not dep:
        raise Range42Error(
            error="not_found", code="NOT_FOUND", status=404,
            message=f"Deployment {deployment_id} not found",
        )
    if not dep.current_attempt_id:
        raise Range42Error(
            error="no_inflight_attempt", code="NO_INFLIGHT_ATTEMPT", status=409,
            message="No in-flight attempt to cancel",
            details=[{"field": "current_attempt_id",
                      "reason": "deployment has no current_attempt_id"}],
        )
    att = await session.get(Attempt, dep.current_attempt_id)
    if att is None or att.state in ("succeeded", "partial", "failed",
                                    "cancelled", "unknown"):
        raise Range42Error(
            error="attempt_terminal", code="ATTEMPT_TERMINAL", status=409,
            message="Current attempt is already terminal",
            details=[{"field": "attempt.state",
                      "reason": f"state={att.state if att else 'missing'}"}],
        )
    # SIGTERM the detached ansible-runner subprocess (Task 20 handle).
    # Events watcher then writes attempt_end with terminal_state=cancelled.
    await signal_running_attempt(dep.workspace_path, signal="SIGTERM")
    att.state = "cancelled"
    att.ended_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(att)
    return AttemptOut.model_validate(att, from_attributes=True)
