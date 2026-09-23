"""Adapt legacy lifecycle actions to the shared attempt reservation path."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import Deployment
from app.schemas.v1.deployments import AttemptCreate, AttemptOut


async def submit_attempt(session: AsyncSession, dep: Deployment, *, scope: str,
                         team_id: int | None = None,
                         operation_vars: dict | None = None) -> AttemptOut:
    from app.routes.v1.deployments.attempts import reserve_attempt

    # These scopes and variables come from typed server routes, never a caller's
    # arbitrary runner arguments. The reservation validates project capabilities.
    payload = AttemptCreate.model_construct(scope=scope, team_id=team_id)
    return await reserve_attempt(dep.id, payload, session, legacy_vars=operation_vars)
