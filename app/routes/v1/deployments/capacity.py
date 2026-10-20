"""Review and retain capacity through existing deployment lifecycle endpoints."""

from datetime import timezone

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import capacity_ledger, db, native_capacity
from app.core.capacity_models import CapacityHold
from app.core.errors import Range42Error
from app.core.models import Deployment, ProxmoxHost
from app.core.physical_capacity import inspect_capacity
from app.core.physical_nodes import target_revision
from app.core.physical_nodes import NodeAttestation
from app.core.physical_node_models import PhysicalNodeBinding
from app.core.preparation_transport import agent_configuration, verify_api_peer, read_agent
from app.routes.v1.deployments.attempts import _session
from app.schemas.v1.capacity_holds import (
    CapacityHoldStatus,
    CapacityHoldReview,
    ReviewCapacity,
    ReserveCapacity,
)

router = APIRouter()


async def deployment(session, deployment_id):
    value = await session.get(Deployment, deployment_id, populate_existing=True)
    if value is None:
        raise Range42Error(
            status=404,
            code="DEPLOYMENT_NOT_FOUND",
            message="The deployment was not found.",
        )
    return value


async def status(session, deployment_id):
    dep = await deployment(session, deployment_id)
    row = await session.scalar(
        select(CapacityHold)
        .where(CapacityHold.deployment_id == dep.id)
        .execution_options(populate_existing=True)
    )
    held = None
    if row:
        record = capacity_ledger.commitment(row)
        root_accepted = native_capacity.root_accepted(row)
        binding = await session.get(PhysicalNodeBinding, dep.target_host_id)
        native_enrolled = bool(binding and NodeAttestation.model_validate_json(binding.observation_json).supported_operations
                               == ['inspect', 'capacity', 'capacity-admit', 'capacity-status'])
        timestamp = (
            row.created_at
            if row.created_at.tzinfo
            else row.created_at.replace(tzinfo=timezone.utc)
        )
        held = {
            "id": row.id,
            "allocation_id": row.allocation_id,
            "physical_node_id": row.physical_node_id,
            "status": row.status,
            "revision": row.revision,
            "approval_revision": row.approval_revision,
            "approved_by": row.approved_by,
            "created_at": timestamp.isoformat(),
            "requirements": [guest.model_dump() for guest in record.guests],
            "reservation_recorded": True,
            "root_accepted": root_accepted,
            "native_acceptance_available": native_enrolled and row.status == 'pending_root' and row.native_request_id is None and not root_accepted,
            "native_reconciliation_available": native_enrolled and row.status == 'pending_root' and row.native_request_id is not None and not root_accepted,
            "native_request_may_have_been_sent": row.native_request_id is not None or row.status in {'active', 'releasing'},
            "unused_retirement_review_available": row.status == 'pending_root' and row.native_request_id is None,
            "retired_by": row.retired_by,
            "retired_at": ((row.retired_at if row.retired_at.tzinfo else row.retired_at.replace(tzinfo=timezone.utc)).isoformat()
                           if row.retired_at else None),
        }
    return {
        "deployment_id": dep.id,
        "applicable": bool(held or dep.native and dep.native.get("workshop_plan")),
        "hold": held,
        "live_verified": False,
        "execution_ready": False,
        "infrastructure_qualified": False,
    }


async def observe(deployment_id):
    factory = db.get_session_factory()
    # Detach the original target before SSH/TLS. No SQL writer or pooled
    # transaction is retained across the native observation.
    async with factory() as session:
        dep = await deployment(session, deployment_id)
        host = await session.get(ProxmoxHost, dep.target_host_id)
        if host is None:
            raise capacity_ledger.refused()
        original = target_revision(host)
        configuration = agent_configuration(host.id, host.node_name)
    observed = await inspect_capacity(configuration)
    await verify_api_peer(host, observed.node)
    if agent_configuration(host.id, host.node_name).revision != configuration.revision:
        raise capacity_ledger.refused()
    async with factory() as session:
        fresh = await session.get(ProxmoxHost, host.id, populate_existing=True)
        if fresh is None or target_revision(fresh) != original:
            raise capacity_ledger.refused()
    return await capacity_ledger.review(
        factory, deployment_id, observed, agent_revision=configuration.revision
    )


@router.get("/{deployment_id}/capacity", response_model=CapacityHoldStatus)
async def get_capacity(
    deployment_id: str, response: Response, session: AsyncSession = Depends(_session)
):
    response.headers["Cache-Control"] = "no-store"
    return await status(session, deployment_id)


@router.post("/{deployment_id}/capacity/review", response_model=CapacityHoldReview)
async def review_capacity(
    deployment_id: str, payload: ReviewCapacity, response: Response
):
    response.headers["Cache-Control"] = "no-store"
    reviewed = await observe(deployment_id)
    return {
        "deployment_id": deployment_id,
        "review_revision": reviewed.revision,
        "physical_node_id": reviewed.observed.node.physical_node_id,
        "node_name": reviewed.observed.node.node_name,
        "observed_at": reviewed.observed_at.isoformat(),
        "requirements": [guest.model_dump() for guest in reviewed.candidate.guests],
        "resources": reviewed.observed.resources.model_dump(),
        "assessment": reviewed.assessment,
        "execution_ready": False,
        "infrastructure_qualified": False,
        "unused_retirement_eligible": reviewed.unused_retirement_eligible,
    }


@router.post("/{deployment_id}/capacity", response_model=CapacityHoldStatus)
async def reserve_capacity(
    deployment_id: str, payload: ReserveCapacity, request: Request, response: Response
):
    response.headers["Cache-Control"] = "no-store"
    reviewed = await observe(deployment_id)
    await capacity_ledger.reserve(
        db.get_session_factory(),
        reviewed,
        actor=request.state.principal.actor_id,
        expected_revision=payload.review_revision,
    )
    async with db.get_session_factory()() as session:
        return await status(session, deployment_id)


@router.delete('/{deployment_id}/capacity', response_model=CapacityHoldStatus)
async def retire_unused_capacity(deployment_id: str, payload: ReserveCapacity, request: Request, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    reviewed = await observe(deployment_id)
    await capacity_ledger.retire_unused(db.get_session_factory(), reviewed,
        actor=request.state.principal.actor_id, expected_revision=payload.review_revision)
    async with db.get_session_factory()() as session:
        return await status(session, deployment_id)


async def native_configuration(deployment_id, reviewed):
    if reviewed.observed.node.supported_operations != ['inspect', 'capacity', 'capacity-admit', 'capacity-status']:
        raise capacity_ledger.refused('CAPACITY_NATIVE_NOT_ENROLLED',
            'This original physical-node inspector has no native acceptance enrollment. Review the explicit node capability before requesting acceptance.')
    async with db.get_session_factory()() as session:
        dep = await deployment(session, deployment_id)
        host = await session.get(ProxmoxHost, dep.target_host_id)
        if host is None:
            raise capacity_ledger.refused()
        selected = agent_configuration(host.id, host.node_name)
        if selected.revision != reviewed.agent_revision:
            raise capacity_ledger.refused()
        return selected


@router.post('/{deployment_id}/capacity/native', response_model=CapacityHoldStatus)
async def accept_native_capacity(deployment_id: str, payload: ReserveCapacity, request: Request, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    reviewed = await observe(deployment_id)
    selected = await native_configuration(deployment_id, reviewed)
    row = await capacity_ledger.journal_native_request(db.get_session_factory(), reviewed,
        actor=request.state.principal.actor_id, expected_revision=payload.review_revision)
    original = native_capacity.request_for(row, selected.installation_id)
    proof = await read_agent(selected, action='capacity-admit', request=original, timeout=110)
    await native_capacity.record_acceptance(db.get_session_factory(), reviewed, original, proof)
    async with db.get_session_factory()() as session:
        return await status(session, deployment_id)


@router.post('/{deployment_id}/capacity/native/reconcile', response_model=CapacityHoldStatus)
async def reconcile_native_capacity(deployment_id: str, payload: ReviewCapacity, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    reviewed = await observe(deployment_id)
    selected = await native_configuration(deployment_id, reviewed)
    async with db.get_session_factory()() as session:
        row, _, _, _, _, _ = await capacity_ledger.current_lifecycle_hold(session, reviewed)
        original = native_capacity.request_for(row, selected.installation_id)
    proof = await read_agent(selected, action='capacity-status', request=original)
    # An absent record cannot prove that an older in-flight RPC will not commit.
    # Keep the original request and its full commitment; never auto-reissue it.
    try:
        parsed = native_capacity.NativeReceipt.model_validate(proof)
    except (ValueError, TypeError):
        raise capacity_ledger.refused('CAPACITY_NATIVE_RECEIPT_MISMATCH', 'The native status reply could not be verified. Preserve the original commitment.') from None
    if parsed.status != 'accepted':
        raise capacity_ledger.refused('CAPACITY_NATIVE_OUTCOME_UNRESOLVED',
            'No original acceptance is recorded on the physical node. The request remains retained; verify native quiescence before retry or retirement.')
    await native_capacity.record_acceptance(db.get_session_factory(), reviewed, original, proof)
    async with db.get_session_factory()() as session:
        return await status(session, deployment_id)
