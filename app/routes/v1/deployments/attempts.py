"""/v1/deployments/:id/attempts — list + create."""
from __future__ import annotations

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
    if payload.scope in {"teardown", "rollback_all", "rollback_team"}:
        raise Range42Error(code="USE_SCOPED_ENDPOINT", status=400,
                           message="Use the teardown or rollback endpoint with its required safeguards")
    from app.core.attempts import submit_attempt
    row = await submit_attempt(session, dep, scope=payload.scope, team_id=payload.team_id)
    return AttemptOut.model_validate(row, from_attributes=True)
