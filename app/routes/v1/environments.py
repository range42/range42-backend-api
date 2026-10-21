"""Administrator review and registration of immutable native environment versions."""
import asyncio
from datetime import datetime
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select, text

from app.core.native_contexts import _api_binding, _contexts
from app.core.native_environment_models import NativeEnvironment
from app.core.native_environment_infrastructure import GatewayFoundation, gateway_capacity, gateway_outside_run
from app.core.native_environments import EnvironmentSelection, RuntimeTarget, refused, registered_contexts, review, review_inputs, selection_for, runtime_target_proof, require_original_runtime_target
from app.core.models import ProxmoxHost
from app.routes.v1.native import _session
from app.core.workshop_profiles import InfrastructureGuest, WorkshopProfileView

router = APIRouter(prefix='/environments', tags=['v1 native environments'])


class GatewayNicCapacity(BaseModel):
    limit: Literal[32]
    occupied: int
    additional_required: int
    available: int
    remaining_after_preparation: int


class EnvironmentReviewOut(BaseModel):
    revision: str
    label: str
    base_context_id: str
    target_host_id: str
    workshop_profile: WorkshopProfileView
    runtime_target: RuntimeTarget | None = None
    gateway_foundation: GatewayFoundation | None = None
    gateway_nic_capacity: GatewayNicCapacity | None = None
    ready_for_planning: Literal[True]
    infrastructure_qualified: Literal[False]
    requirements: list[str]


class EnvironmentOut(BaseModel):
    id: str
    label: str
    base_context_id: str
    target_host_id: str
    target_node: str
    workshop_profile: WorkshopProfileView
    runtime_target: RuntimeTarget | None = None
    gateway_foundation: GatewayFoundation | None = None
    gateway_nic_capacity: GatewayNicCapacity | None = None
    ready_for_planning: bool
    infrastructure_qualified: Literal[False]
    issues: list[str]
    created_at: datetime
    approved_by: str


class BaseContextOut(BaseModel):
    id: str
    label: str
    target_node: str
    target_api_url: str
    ready: bool
    issues: list[str]


class EnvironmentPage(BaseModel):
    items: list[EnvironmentOut]
    total: int
    base_contexts: list[BaseContextOut]


class InfrastructureSelection(EnvironmentSelection):
    vmids: list[Annotated[int, Field(ge=100, le=999999999)]] = Field(min_length=1, max_length=32)


class FoundationSelection(EnvironmentSelection):
    vmid: int = Field(strict=True, ge=100, le=999999999)

    @model_validator(mode='after')
    def separate_gateway(self):
        if not gateway_outside_run(self.vmid, self.workshop_profile):
            raise ValueError('The retained gateway must be separate from participant guests and source templates')
        return self


class FoundationOut(BaseModel):
    gateway_foundation: GatewayFoundation
    nic_capacity: GatewayNicCapacity
    infrastructure_qualified: Literal[False]


class InfrastructureOut(BaseModel):
    guests: list[InfrastructureGuest]


class EnvironmentRegistration(EnvironmentSelection):
    review_revision: str = Field(pattern=r'^[a-f0-9]{64}$')


class Revalidation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    review_revision: str = Field(pattern=r'^[a-f0-9]{64}$')


async def record(session, identity):
    row = await session.get(NativeEnvironment, identity)
    if row is None:
        from app.core.errors import Range42Error
        raise Range42Error(status=404, code='NATIVE_ENVIRONMENT_NOT_FOUND', message='Native environment not found.')
    return row


async def unchanged_target(session, row):
    host = await session.get(ProxmoxHost, row.target_host_id)
    try:
        valid = (host is not None and host.node_name == row.target_node
            and _api_binding(host.api_url) == _api_binding(row.target_api_url))
    except ValueError:
        valid = False
    if not valid:
        raise refused('This environment’s node or HTTPS API endpoint changed. Create a separately reviewed environment version.')
    require_original_runtime_target(row, await runtime_target_proof(session, selection_for(row), host))


def public_record(row, *, ready=False, issues=None):
    selected = selection_for(row)
    return {'id': row.id, 'label': row.label, 'base_context_id': row.base_context_id,
        'target_host_id': row.target_host_id, 'target_node': row.target_node,
        'workshop_profile': selected.workshop_profile.public(),
        'runtime_target': RuntimeTarget.model_validate_json(row.runtime_target_json).model_dump() if row.runtime_target_json else None,
        'gateway_foundation': selected.gateway_foundation.model_dump() if selected.gateway_foundation else None,
        'gateway_nic_capacity': gateway_capacity(selected.gateway_foundation, selected.workshop_profile.networks) if selected.gateway_foundation else None,
        'ready_for_planning': ready, 'infrastructure_qualified': False,
        'issues': issues or [], 'created_at': row.created_at, 'approved_by': row.approved_by}


@router.get('', response_model=EnvironmentPage)
async def listing(session=Depends(_session)):
    rows = list(await session.scalars(select(NativeEnvironment).order_by(NativeEnvironment.created_at, NativeEnvironment.id)))
    contexts = {item.id: item for item in await registered_contexts(session)}
    bases = await asyncio.to_thread(_contexts)
    items = [public_record(row, ready=not contexts[row.id].issues(), issues=contexts[row.id].issues()) for row in rows]
    return {'items': items, 'total': len(items), 'base_contexts': [
        {'id': base.id, 'label': base.label, 'target_node': base.target_node,
         'target_api_url': base.target_api_url, 'ready': not base.issues(),
         'issues': base.issues()} for base in bases if base.target_api_url and base.target_node]}


@router.post('/foundation/review', response_model=FoundationOut)
async def foundation(payload: FoundationSelection, session=Depends(_session)):
    from app.core.native_environment_infrastructure import observe_foundation
    selection = EnvironmentSelection.model_validate(payload.model_dump(exclude={'vmid'}))
    _, host = await review_inputs(session, selection)
    return await observe_foundation(host, payload.vmid, payload.workshop_profile.networks)


@router.post('/infrastructure/review', response_model=InfrastructureOut)
async def infrastructure(payload: InfrastructureSelection, session=Depends(_session)):
    from app.core.native_environment_infrastructure import observe
    selection = EnvironmentSelection.model_validate(payload.model_dump(exclude={'vmids'}))
    _, host = await review_inputs(session, selection)
    guests = await observe(host, payload.vmids, {network.bridge for network in payload.workshop_profile.networks})
    return {'guests': guests}


@router.post('/review', response_model=EnvironmentReviewOut)
async def preview(payload: EnvironmentSelection, session=Depends(_session)):
    return await review(session, payload)


@router.post('', status_code=201, response_model=EnvironmentOut)
async def create(payload: EnvironmentRegistration, request: Request, session=Depends(_session)):
    selection = EnvironmentSelection.model_validate(payload.model_dump(exclude={'review_revision'}))
    current = await review(session, selection)
    if current['revision'] != payload.review_revision:
        raise refused('Environment inputs or credentials changed. Review the current configuration before saving.')
    # Live reads happen before the writer lock. Serialize the final local
    # review and insert so repeated/lost responses retain the same environment.
    await session.rollback()
    await session.execute(text('BEGIN IMMEDIATE'))
    fresh, _ = await review_inputs(session, selection)
    if fresh['revision'] != payload.review_revision:
        raise refused('Environment inputs changed during registration. Review the current configuration before saving.')
    existing = await session.scalar(select(NativeEnvironment).where(
        NativeEnvironment.review_revision == payload.review_revision).order_by(
        NativeEnvironment.created_at, NativeEnvironment.id).limit(1))
    if existing:
        await session.commit()
        return public_record(existing, ready=True)
    if await session.scalar(select(func.count()).select_from(NativeEnvironment)) >= 256:
        raise refused('The native environment registry is full.')
    host = await session.get(ProxmoxHost, payload.target_host_id)
    row = NativeEnvironment(id='env-' + uuid.uuid4().hex[:24], label=payload.label,
        base_context_id=payload.base_context_id, target_host_id=payload.target_host_id,
        target_node=host.node_name, target_api_url=host.api_url,
        profile_json=payload.workshop_profile.model_dump_json(),
        foundation_json=payload.gateway_foundation.model_dump_json() if payload.gateway_foundation else None,
        runtime_target_json=RuntimeTarget.model_validate(fresh['runtime_target']).model_dump_json() if fresh['runtime_target'] else None,
        review_revision=payload.review_revision,
        approved_by=request.state.principal.actor_id)
    session.add(row)
    await session.commit()
    return public_record(row, ready=True)


@router.post('/{environment_id}/review', response_model=EnvironmentReviewOut)
async def review_existing(environment_id: str, session=Depends(_session)):
    row = await record(session, environment_id)
    await unchanged_target(session, row)
    current, _ = await review_inputs(session, selection_for(row))
    return current


@router.post('/{environment_id}/revalidate', response_model=EnvironmentOut)
async def revalidate(environment_id: str, payload: Revalidation, request: Request, session=Depends(_session)):
    row = await record(session, environment_id)
    await unchanged_target(session, row)
    # Preserve credential rotation and owned cleanup when a retained gateway is
    # offline. New bindings are observed at registration; execution checks its
    # original pins again before any guest is started.
    current, _ = await review_inputs(session, selection_for(row))
    if current['revision'] != payload.review_revision:
        raise refused('Environment inputs changed during review. Refresh and review again.')
    row.review_revision = payload.review_revision
    row.approved_by = request.state.principal.actor_id
    await session.commit()
    return public_record(row, ready=True)
