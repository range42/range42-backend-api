"""Administrator review and registration of immutable native environment versions."""
import asyncio
from datetime import datetime
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.core.native_contexts import _api_binding, _contexts
from app.core.native_environment_models import NativeEnvironment
from app.core.native_environments import EnvironmentSelection, refused, registered_contexts, review, review_inputs, selection_for
from app.core.models import ProxmoxHost
from app.routes.v1.native import _session
from app.core.workshop_profiles import InfrastructureGuest, WorkshopProfileView

router = APIRouter(prefix='/environments', tags=['v1 native environments'])


class EnvironmentReviewOut(BaseModel):
    revision: str
    label: str
    base_context_id: str
    target_host_id: str
    workshop_profile: WorkshopProfileView
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


def public_record(row, *, ready=False, issues=None):
    return {'id': row.id, 'label': row.label, 'base_context_id': row.base_context_id,
        'target_host_id': row.target_host_id, 'target_node': row.target_node,
        'workshop_profile': selection_for(row).workshop_profile.public(),
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
    if await session.scalar(select(func.count()).select_from(NativeEnvironment)) >= 256:
        raise refused('The native environment registry is full.')
    host = await session.get(ProxmoxHost, payload.target_host_id)
    row = NativeEnvironment(id='env-' + uuid.uuid4().hex[:24], label=payload.label,
        base_context_id=payload.base_context_id, target_host_id=payload.target_host_id,
        target_node=host.node_name, target_api_url=host.api_url,
        profile_json=payload.workshop_profile.model_dump_json(), review_revision=payload.review_revision,
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
