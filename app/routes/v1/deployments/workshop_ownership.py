"""Original ownership import stays inside the existing deployment lifecycle."""

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import workshop_ownership as ownership
from app.core.workshop_ownership_transport import observe
from app.routes.v1.admin import workshop
from app.routes.v1.deployments.attempts import _session
from app.schemas.v1.workshop_ownership import (
    InspectOwnership,
    SaveOwnership,
    OwnershipReview,
    OwnershipStatus,
)

router = APIRouter()


@router.get("/{deployment_id}/allocations/import", response_model=OwnershipStatus)
async def import_status(
    deployment_id: str, response: Response, session: AsyncSession = Depends(_session)
):
    response.headers["Cache-Control"] = "no-store"
    return await ownership.status(session, deployment_id)


@router.post(
    "/{deployment_id}/allocations/import/review", response_model=OwnershipReview
)
async def review_import(
    deployment_id: str,
    payload: InspectOwnership,
    response: Response,
    session: AsyncSession = Depends(_session),
):
    response.headers["Cache-Control"] = "no-store"
    observed = await observe(session, deployment_id)
    return {
        "deployment_id": deployment_id,
        "revision": observed.revision,
        "witness": observed.witness,
        "capacity_borrowing_allowed": False,
        "remote_quiescence_verified": False,
    }


@router.post("/{deployment_id}/allocations/import", response_model=OwnershipStatus)
async def save_import(
    deployment_id: str,
    payload: SaveOwnership,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(_session),
):
    response.headers["Cache-Control"] = "no-store"
    observed = await observe(session, deployment_id)
    return await ownership.save_observation(
        session,
        observed,
        review_revision=payload.review_revision,
        actor=request.state.principal.actor_id,
        rpc=workshop.agent_request,
    )
