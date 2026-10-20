"""Named administrators close/resume exact retained run allocations."""

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import deployment_admission
from app.routes.v1.deployments.attempts import _session
from app.schemas.v1.deployment_admission import DeploymentAdmissionChange, DeploymentAdmissionState

router = APIRouter()


@router.get("/{deployment_id}/write-admission", response_model=DeploymentAdmissionState)
async def get_admission(
    deployment_id: str, response: Response, session: AsyncSession = Depends(_session)
):
    response.headers["Cache-Control"] = "no-store"
    return await deployment_admission.snapshot(session, deployment_id)


@router.post("/{deployment_id}/write-admission", response_model=DeploymentAdmissionState)
async def change_admission(
    deployment_id: str,
    payload: DeploymentAdmissionChange,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(_session),
):
    response.headers["Cache-Control"] = "no-store"
    return await deployment_admission.change(
        session,
        deployment_id,
        actor=request.state.principal.actor_id,
        **payload.model_dump(exclude={"confirmation"}),
    )
