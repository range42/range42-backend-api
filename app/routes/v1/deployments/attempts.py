"""/v1/deployments/:id/attempts — list + create."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session_factory
from app.core.errors import Range42Error
from app.core.models import Attempt, Deployment
from app.schemas.v1.common import Page
from app.schemas.v1.deployments import AttemptCreate, AttemptOut

router = APIRouter()


async def _session() -> AsyncSession:
    async with get_session_factory()() as session:
        yield session


@router.get("/{deployment_id}/attempts", response_model=Page[AttemptOut])
async def list_attempts(deployment_id: str,
                        session: AsyncSession = Depends(_session)):
    rows = (await session.execute(
        select(Attempt).where(Attempt.deployment_id == deployment_id))).scalars().all()
    return Page[AttemptOut](
        items=[AttemptOut.model_validate(r, from_attributes=True) for r in rows],
        total=len(rows), offset=0, limit=len(rows),
    )


@router.post("/{deployment_id}/attempts", response_model=AttemptOut,
             status_code=status.HTTP_201_CREATED)
async def create_attempt(deployment_id: str, payload: AttemptCreate,
                         session: AsyncSession = Depends(_session)):
    dep = (await session.execute(
        select(Deployment).where(Deployment.id == deployment_id))).scalar_one_or_none()
    if dep is None:
        raise Range42Error(
            error="not_found", code="NOT_FOUND", status=404,
            message=f"Deployment {deployment_id} not found",
        )
    # Spec §13 cut-list: single-attempt-per-deployment for v1 cut.
    # Existing attempts stay queryable; new attempt transitions deployment
    # out of terminal state. The full multi-attempt lifecycle is additive
    # in v1.1 — attempt_id field already in event schema.
    row = Attempt(
        id=uuid.uuid4().hex[:16],
        deployment_id=deployment_id,
        scope=payload.scope,
        team_id=payload.team_id,
        state="pending",
        started_at=datetime.now(timezone.utc),
    )
    session.add(row)
    dep.current_attempt_id = row.id
    dep.state = "pending"
    await session.commit()
    await session.refresh(row)

    import os
    if os.getenv("RANGE42_AUTO_START_ATTEMPTS", "1") in ("1", "true", "yes"):
        try:
            from app.core.deploy_trigger import start_attempt
            await start_attempt(session, attempt=row)
        except Exception as e:  # noqa: BLE001
            from app.core.logging import get_logger
            get_logger(__name__).warning("attempt_autostart_failed",
                                         attempt_id=row.id, err=str(e))

    return AttemptOut.model_validate(row, from_attributes=True)
