"""DELETE /v1/deployments/:id — teardown attempt with confirm-phrase gate.

Enqueues a teardown-scoped attempt only after confirm_codename matches
the deployment codename exactly. Refuses teardown when the deployment is
in the 'unknown' terminal state per spec §12.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session_factory
from app.core.errors import Range42Error
from app.core.models import Deployment
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

    from app.core.attempts import submit_attempt
    att = await submit_attempt(session, dep, scope="teardown")
    # Preserve inventory, credentials and audit logs, even after success.
    # Filesystem retention is independent of infrastructure teardown.
    return AttemptOut.model_validate(att, from_attributes=True)
