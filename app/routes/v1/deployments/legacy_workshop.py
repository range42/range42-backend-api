"""Compatibility operations on the imported parent; no overlapping children."""

from copy import deepcopy
from typing import Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import legacy_workshop_operations as legacy
from app.core.db import get_session_factory
from app.core.errors import Range42Error
from app.core.models import Deployment
from app.routes.v1.deployments.attempts import reserve_attempt
from app.routes.v1.deployments import preflight
from app.schemas.v1.deployments import (
    AttemptCreate,
    AttemptOut,
    PreflightRequest,
    PreflightResponse,
)

router = APIRouter()
PREFIX = "/{deployment_id}/workshop/legacy/participants/{username}"


class ParticipantPreflight(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["deploy", "configure", "reset", "teardown"]


class ParticipantAction(ParticipantPreflight):
    dispatch_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    confirm_username: str | None = None


async def _session():
    async with get_session_factory()() as session:
        yield session


@router.post(PREFIX + "/preflight", response_model=PreflightResponse)
async def participant_preflight(
    deployment_id: str,
    username: str,
    payload: ParticipantPreflight,
    response: Response,
    session: AsyncSession = Depends(_session),
):
    response.headers["Cache-Control"] = "no-store"
    selected = await legacy.operation(session, deployment_id, username, payload.action)
    original = await session.get(Deployment, deployment_id)
    # Detached/transient copy: preparing a seat must never flush changes to the
    # cohort's source parameters or alter another participant's scope.
    dep = Deployment(
        **{
            column.name: deepcopy(getattr(original, column.name))
            for column in Deployment.__table__.columns
        }
    )
    dep.native = {
        **dep.native,
        "parameters": await legacy.parameters(session, original, selected),
    }
    return await preflight.evaluate_preflight(
        session,
        dep,
        PreflightRequest(
            scope="full" if payload.action == "deploy" else payload.action
        ),
    )


@router.post(PREFIX + "/actions", response_model=AttemptOut, status_code=201)
async def participant_action(
    deployment_id: str,
    username: str,
    payload: ParticipantAction,
    response: Response,
    session: AsyncSession = Depends(_session),
):
    response.headers["Cache-Control"] = "no-store"
    if (
        payload.action in {"reset", "teardown"} and payload.confirm_username != username
    ) or (
        payload.confirm_username is not None and payload.confirm_username != username
    ):
        raise Range42Error(
            status=400,
            code="WORKSHOP_CONFIRM_MISMATCH",
            message="Confirm the selected participant username.",
        )
    selected = await legacy.operation(session, deployment_id, username, payload.action)
    dep = await session.get(Deployment, deployment_id)
    payload_attempt = AttemptCreate(
        scope="full" if payload.action == "deploy" else payload.action,
        confirm_codename=dep.codename
        if payload.action in {"reset", "teardown"}
        else None,
    )
    return await reserve_attempt(
        deployment_id,
        payload_attempt,
        session,
        operation=selected,
        dispatch_id=payload.dispatch_id,
    )
