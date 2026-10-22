"""Catalog foundation previews and exact, server-derived Core reservations."""
import asyncio
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from app.core import config, db
from app.core.allocation_ledger import load_installed_reservations
from app.core.allocation_reservations import reserve, token_digest
from app.core.errors import Range42Error
from app.core.models import ProxmoxHost
from app.core.native_environments import resolve_registered_context
from app.core.proxmox_tls import proxmox_verify
from app.core.template_preparation import PreparationSpec, resolve_native_foundation, foundation_topology, allocation_authority
from app.schemas.v1.allocation import AllocationRequest, AllocationLease
from app.core import foundation_execution
from app.core.capacity_models import CapacityHold
from app.core.models import Deployment
from app.core.physical_capacity import StrictObservation
from sqlalchemy import select
from typing import Literal

router = APIRouter(tags=['v1 native foundations'])


async def session():
    async with db.get_session_factory()() as selected:
        yield selected


class FoundationPreview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    sha: str = Field(pattern=r'^(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})$')
    path: str = Field(min_length=1, max_length=1024)
    component_id: str | None = Field(default=None, pattern=r'^[A-Za-z][A-Za-z0-9_.-]{0,127}$')
    context_id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')
    target_host_id: str = Field(min_length=1, max_length=128)
    foundation: PreparationSpec
    prepare_environment_networks: StrictBool = False


class FoundationReservation(FoundationPreview):
    project_key: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$')
    lease_seconds: int = Field(default=3600, strict=True, ge=300, le=86400)


async def reviewed(project_id, payload, selected):
    from app.routes.v1.native import preview
    descriptor = await preview(project_id, payload.path, payload.sha, payload.component_id, selected)
    host = await selected.get(ProxmoxHost, payload.target_host_id)
    if host is None:
        raise Range42Error(code='NOT_FOUND', status=404, message='The selected target was not found.')
    context = await resolve_registered_context(selected, payload.context_id, host)
    plan = resolve_native_foundation({'foundation': payload.foundation.model_dump()}, descriptor, context, run_id='0'*16)
    if plan is None:
        raise Range42Error(code='FOUNDATION_REVIEW_REQUIRED', status=409, message='Choose the explicit template preparation scenario.')
    ids = [row['vm_id'] for row in plan['templates']]
    request = {'vmid_start': min(ids), 'vmid_end': max(ids), 'networks': [],
        'vms': [{'node_id': row['node_id'], 'vm_id': row['vm_id'], 'nics': []} for row in plan['assignments']]}
    authority = allocation_authority(payload.foundation, context, host, run_id='0'*16)
    network_plan=None
    if payload.prepare_environment_networks:
        from app.core.environment_networks import compile_plan
        network_plan=compile_plan(context)
    return descriptor, host, plan, request, authority, network_plan


@router.post('/projects/{project_id}/native-scenario/foundation/preview')
async def preview_foundation(project_id: str, payload: FoundationPreview, selected=Depends(session)):
    descriptor, _, plan, allocation, _, networks = await reviewed(project_id, payload, selected)
    topology=foundation_topology(plan)
    if networks is not None:
        topology['networks']=[{'vnet':row['bridge'],'subnet':row['subnet'],'gateway':row['gateway']} for row in networks['networks']]
        management=networks['management']
        topology['networks'].append({'vnet':management['bridge'],'subnet':management['subnet'],'gateway':management['host_address'].split('/')[0]})
    return {'environment_networks': networks, 'preview_only': True, 'resources_reserved': False, 'execution_ready': False,
        'assignments': plan['assignments'], 'allocation_request': allocation, 'capacity': plan['capacity'],
        'topology': {**descriptor['topology'], **topology,
            'reservations': {'status': 'missing', 'issues': ['Reserve these exact template copies before creating their foundation deployment.']}}}


@router.post('/projects/{project_id}/native-scenario/foundation/reservations', response_model=AllocationLease)
async def reserve_foundation(project_id: str, payload: FoundationReservation,
                             x_range42_reservation_token: str = Header(min_length=32, max_length=128)):
    token_digest(x_range42_reservation_token)
    factory = db.get_session_factory()
    async with factory() as selected:
        _, host, _, allocation, authority, _ = await reviewed(project_id, payload, selected)
    if not config.settings.wwwapp_playbooks_dir:
        raise Range42Error(status=409, code='ALLOCATION_OCCUPANCY_UNAVAILABLE', message='Configure the installed scenario reservation registry.')
    reserved = await asyncio.to_thread(load_installed_reservations, Path(config.settings.wwwapp_playbooks_dir))
    request = AllocationRequest.model_validate({**allocation, 'project_key': payload.project_key, 'lease_seconds': payload.lease_seconds})
    async with httpx.AsyncClient(verify=proxmox_verify(), timeout=10, follow_redirects=False) as client:
        return await reserve(factory, host, request, x_range42_reservation_token, client,
            reserved=reserved, template_authority=authority)


class FoundationReview(StrictObservation):
    pass


class FoundationStatus(StrictObservation):
    deployment_id: str
    request_recorded: bool
    preparation_available: bool
    receipt: foundation_execution.FoundationReceipt | None
    issue: str
    execution_ready: Literal[False] = False
    infrastructure_qualified: Literal[False] = False


async def foundation_status(deployment_id, selected):
    dep = await selected.get(Deployment, deployment_id)
    if dep is None or not dep.native or not dep.native.get('foundation_plan'):
        raise Range42Error(status=404, code='FOUNDATION_NOT_FOUND', message='This deployment does not own a reusable template foundation.')
    row = await selected.scalar(select(CapacityHold).where(CapacityHold.deployment_id == deployment_id))
    value = {'deployment_id': deployment_id, 'request_recorded': False, 'preparation_available': False,
             'receipt': None, 'issue': 'Review and physically accept this foundation’s original capacity in Overview.'}
    if row is not None:
        from app.core import capacity_ledger
        capacity_ledger.commitment(row)
        value['request_recorded'] = row.preparation_request_json is not None
        try:
            request, _, _ = await foundation_execution.original(selected, row)
            if row.preparation_receipt_json is not None:
                import json
                value['receipt'] = foundation_execution.verified_receipt(request, json.loads(row.preparation_receipt_json))
            if row.preparation_request_json is not None:
                await foundation_execution.assert_dispatch(selected, row)
                value.update(preparation_available=True, issue='')
            else:
                value['issue'] = 'Review and retain the original template preparation request before starting the deployment.'
        except Range42Error as exc:
            value['issue'] = exc.message
    return value


@router.get('/deployments/{deployment_id}/foundation', response_model=FoundationStatus)
async def get_foundation(deployment_id: str, response: Response, selected=Depends(session)):
    response.headers['Cache-Control'] = 'no-store'
    return await foundation_status(deployment_id, selected)


@router.post('/deployments/{deployment_id}/foundation/review', response_model=FoundationStatus)
async def review_foundation(deployment_id: str, payload: FoundationReview, response: Response):
    from app.routes.v1.deployments.capacity import observe
    response.headers['Cache-Control'] = 'no-store'
    reviewed = await observe(deployment_id)
    await foundation_execution.journal(db.get_session_factory(), reviewed)
    async with db.get_session_factory()() as selected:
        return await foundation_status(deployment_id, selected)


@router.post('/deployments/{deployment_id}/foundation/reconcile', response_model=FoundationStatus)
async def reconcile_foundation(deployment_id: str, payload: FoundationReview, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    await foundation_execution.reconcile(db.get_session_factory(), deployment_id)
    async with db.get_session_factory()() as selected:
        return await foundation_status(deployment_id, selected)


class NetworkStatus(StrictObservation):
    deployment_id: str
    requested: bool
    environment_id: str | None
    request_recorded: bool
    preparation_available: bool
    plan: dict | None
    receipt: dict | None
    issue: str
    execution_ready: Literal[False] = False


async def network_status(deployment_id, selected):
    from app.core import environment_network_execution as networking
    dep = await selected.get(Deployment, deployment_id)
    if dep is None or not dep.native or not dep.native.get('foundation_plan'):
        raise Range42Error(status=404, code='FOUNDATION_NOT_FOUND', message='This deployment does not own a reusable foundation.')
    requested = dep.native.get('prepare_environment_networks') is True
    value = {'deployment_id': deployment_id, 'requested': requested,
        'environment_id': dep.native['context_id'] if requested else None,
        'request_recorded': False, 'preparation_available': False, 'plan': None, 'receipt': None,
        'issue': '' if not requested else 'Review and retain the original bridge request before starting this foundation.'}
    if requested:
        from app.core.native_environment_models import NativeEnvironment
        environment = await selected.get(NativeEnvironment, dep.native['context_id'])
        value['request_recorded'] = environment is not None and environment.network_preparation_json is not None
        try:
            *_, bundle = await networking.original(selected, deployment_id)
            value.update(preparation_available=True, plan=bundle['request']['plan'], receipt=bundle['receipt'], issue='')
        except Range42Error as error:
            value['issue'] = error.message
    return value


@router.get('/deployments/{deployment_id}/foundation/networks', response_model=NetworkStatus)
async def get_networks(deployment_id: str, response: Response, selected=Depends(session)):
    response.headers['Cache-Control'] = 'no-store'
    return await network_status(deployment_id, selected)


@router.post('/deployments/{deployment_id}/foundation/networks/review', response_model=NetworkStatus)
async def review_networks(deployment_id: str, payload: FoundationReview, response: Response):
    from app.core import environment_network_execution as networking
    response.headers['Cache-Control'] = 'no-store'
    await networking.journal(db.get_session_factory(), deployment_id)
    async with db.get_session_factory()() as selected:
        return await network_status(deployment_id, selected)


@router.post('/deployments/{deployment_id}/foundation/networks/reconcile', response_model=NetworkStatus)
async def reconcile_networks(deployment_id: str, payload: FoundationReview, response: Response):
    from app.core import environment_network_execution as networking
    response.headers['Cache-Control'] = 'no-store'
    await networking.reconcile(db.get_session_factory(), deployment_id)
    async with db.get_session_factory()() as selected:
        return await network_status(deployment_id, selected)
