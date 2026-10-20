"""Administrator review of an optional, server-observed physical node binding."""

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Range42Error
from app.core.physical_node_models import PhysicalNodeBinding
from app.core.physical_nodes import (
    public_binding,
    review_observation,
    review_required,
    save_observation,
    target_revision,
)
from app.core.preparation_transport import (
    agent_configuration,
    inspect_agent,
    verify_api_peer,
)
from app.routes.v1.proxmox._helpers import _get_host, _session
from app.schemas.v1.physical_nodes import (
    InspectNode,
    PhysicalNodeBindingOut,
    PhysicalNodeReview,
    PhysicalNodeStatus,
    SavePhysicalNode,
)

router = APIRouter(prefix="/hosts/{host_id}/physical-node")


async def alias_count(session, physical_node_id):
    return await session.scalar(
        select(func.count())
        .select_from(PhysicalNodeBinding)
        .where(PhysicalNodeBinding.physical_node_id == physical_node_id)
    )


@router.get("", response_model=PhysicalNodeStatus)
async def status(
    host_id: str, response: Response, session: AsyncSession = Depends(_session)
):
    response.headers["Cache-Control"] = "no-store"
    host = await _get_host(host_id, session)
    configuration_state = "unavailable"
    configuration = None
    try:
        configuration = agent_configuration(host_id, host.node_name)
        configuration_state = "enrolled"
    except Range42Error:
        pass
    binding = await session.get(PhysicalNodeBinding, host_id)
    current = False
    result = None
    if binding:
        result = {
            **public_binding(binding),
            "alias_count": await alias_count(session, binding.physical_node_id),
        }
        current = bool(
            configuration
            and binding.agent_revision == configuration.revision
            and binding.target_revision == target_revision(host)
        )
    return {
        "target_host_id": host_id,
        "configuration_state": configuration_state,
        "binding": result,
        "needs_review": not current,
    }


async def observe(session, host_id):
    host = await _get_host(host_id, session)
    target = target_revision(host)
    configuration = agent_configuration(host_id, host.node_name)
    attestation = await inspect_agent(configuration)
    await verify_api_peer(host, attestation)
    if agent_configuration(host_id, host.node_name).revision != configuration.revision:
        raise review_required()
    return await review_observation(
        session,
        host_id,
        attestation,
        agent_revision=configuration.revision,
        target_revision_at_observation=target,
    )


@router.post("/review", response_model=PhysicalNodeReview)
async def review(
    host_id: str,
    request: InspectNode,
    response: Response,
    session: AsyncSession = Depends(_session),
):
    response.headers["Cache-Control"] = "no-store"
    observation = await observe(session, host_id)
    return {
        **observation.attestation.model_dump(),
        "target_host_id": host_id,
        "revision": observation.revision,
    }


@router.post("", response_model=PhysicalNodeBindingOut)
async def register(
    host_id: str,
    request: SavePhysicalNode,
    response: Response,
    http_request: Request,
    session: AsyncSession = Depends(_session),
):
    response.headers["Cache-Control"] = "no-store"
    observed = await observe(session, host_id)
    result = await save_observation(
        session,
        observed,
        review_revision=request.review_revision,
        actor=http_request.state.principal.actor_id,
    )
    return {
        **result,
        "alias_count": await alias_count(session, result["physical_node_id"]),
    }
