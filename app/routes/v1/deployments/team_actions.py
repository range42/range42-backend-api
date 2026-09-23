"""Per-team reset attempts."""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session_factory
from app.core.errors import Range42Error
from app.core.models import Deployment
from app.schemas.v1.deployments import AttemptOut

router = APIRouter()


async def _session() -> AsyncSession:
    async with get_session_factory()() as session:
        yield session


@router.post("/{deployment_id}/teams/{team_id}/reset", response_model=AttemptOut,
             status_code=status.HTTP_202_ACCEPTED)
async def reset_team(deployment_id: str, team_id: int,
                     session: AsyncSession = Depends(_session)):
    dep = (await session.execute(
        select(Deployment).where(Deployment.id == deployment_id))).scalar_one_or_none()
    if dep is None:
        raise Range42Error(
            error="not_found", code="NOT_FOUND", status=404,
            message=f"Deployment {deployment_id} not found",
        )
    if team_id < 1 or team_id > dep.team_count:
        raise Range42Error(
            error="bad_team", code="TEAM_OUT_OF_RANGE", status=400,
            message=f"team_id {team_id} outside 1..{dep.team_count}",
            details=[{"field": "team_id",
                      "reason": f"must be in 1..{dep.team_count}"}],
        )
    from app.core.attempts import submit_attempt
    att = await submit_attempt(session, dep, scope="team_reset", team_id=team_id)
    return AttemptOut.model_validate(att, from_attributes=True)
