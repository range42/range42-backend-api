"""Run an explicit teardown playbook after exact codename confirmation.

Workspace files and attempt history remain available after teardown.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session_factory
from app.routes.v1.deployments.attempts import create_attempt
from app.schemas.v1.deployments import AttemptCreate, AttemptOut, TeardownRequest

router = APIRouter()


async def _session() -> AsyncSession:
    async with get_session_factory()() as session:
        yield session


@router.delete("/{deployment_id}", response_model=AttemptOut,
               status_code=status.HTTP_202_ACCEPTED)
async def teardown(deployment_id: str,
                   payload: TeardownRequest,
                   session: AsyncSession = Depends(_session)):
    return await create_attempt(
        deployment_id,
        AttemptCreate(scope="teardown", confirm_codename=payload.confirm_codename),
        session,
    )
