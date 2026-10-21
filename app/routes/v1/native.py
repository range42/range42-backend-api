"""Native context discovery and read-only saved-scenario previews."""
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from pydantic import BaseModel, ConfigDict, Field

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session_factory
from app.core.models import ProxmoxHost
from app.core.native_contexts import available_contexts
from app.core.native_scenarios import inside, inspect_native_scenario
from app.core.scenario import checkout_project_repository, checkout_native_component
from app.core.workshop_runs import WorkshopSpec, resolve_native_workshop, validate_activation, workshop_topology

router = APIRouter(tags=["v1 native scenarios"])


async def _session():
    async with get_session_factory()() as session:
        yield session


@router.get("/contexts")
async def contexts(session: AsyncSession = Depends(_session)):
    hosts = list((await session.execute(select(ProxmoxHost))).scalars())
    from app.core.native_environments import registered_contexts
    registered = await registered_contexts(session)
    rows = await asyncio.to_thread(available_contexts, hosts, registered=registered)
    return {"items": rows, "total": len(rows), "setup_hint": (
        "Initialize an environment with range42-context init on the deployer-cli. "
        "Run this backend there as the deployment user and configure RANGE42_CONTEXT_ROOT "
        "and RANGE42_CONTEXT_SCRIPT, or register existing workspaces with RANGE42_NATIVE_CONTEXTS_FILE.")}


@router.get("/projects/{project_id}/native-scenario")
async def preview(project_id: str, path: str = Query(min_length=1, max_length=1024),
                  sha: str = Query(pattern=r"^(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})$"),
                  component_id: str | None = Query(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$"),
                  session: AsyncSession = Depends(_session)):
    with TemporaryDirectory(prefix="r42-native-preview-") as directory:
        root, _, project = await checkout_project_repository(session, project_id,
            dest=Path(directory) / "checkout", sha=sha)
        if project.subdir:
            root = inside(root, project.subdir)
        if component_id:
            root, _ = await checkout_native_component(session, root, component_id, path, dest=Path(directory) / "origin")
        return await asyncio.to_thread(inspect_native_scenario, root, path)


class WorkshopPreview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    sha: str = Field(pattern=r'^(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})$')
    path: str = Field(min_length=1, max_length=1024)
    component_id: str | None = Field(default=None, pattern=r'^[A-Za-z][A-Za-z0-9_.-]{0,127}$')
    workshop: WorkshopSpec


@router.post('/projects/{project_id}/native-scenario/preview')
async def preview_workshop(project_id: str, request: WorkshopPreview, session: AsyncSession = Depends(_session)):
    descriptor = await preview(project_id, request.path, request.sha, request.component_id, session)
    plan = resolve_native_workshop({'workshop': request.workshop.model_dump()}, descriptor, run_id='0' * 16)
    validate_activation(plan)
    allocation = {'vmid_start': plan['vmid_start'],
        'vmid_end': plan['vmid_start'] + 2 * plan['seats'] - 1,
        'networks': [{'network_id': net['vnet'], 'bridge': net['vnet'],
            'subnet': net['subnet'], 'gateway': net['gateway']} for net in plan['networks']],
        'vms': [{'node_id': f"{guest['username']}-{guest['role']}", 'vm_id': guest['vm_id'],
            'nics': [{'index': 0, 'network_id': guest['bridge'], 'ip': guest['ip']}]} for guest in plan['guests']]}
    if gateway := plan.get('managed_gateway'):
        gateway_node = 'managed-gateway'
        for network in allocation['networks']:
            network['gateway_node_id'] = gateway_node
        management = {'network_id': gateway['management_bridge'], 'bridge': gateway['management_bridge'],
                      'subnet': gateway['management_subnet'], 'gateway': gateway['management_gateway']}
        allocation['networks'].append(management)
        allocation['vms'].append({'node_id': gateway_node, 'vm_id': gateway['vm_id'],
            'nics': [{'index': nic['index'], 'network_id': nic['bridge'], 'ip': nic['ip']}
                     for nic in gateway['nics']]})
        identities = [vm['vm_id'] for vm in allocation['vms']]
        allocation.update(vmid_start=min(identities), vmid_end=max(identities))
    return {'preview_only': True, 'resources_reserved': False, 'assignments': plan['assignments'],
            'allocation_request': allocation,
            'capacity': plan['capacity'], 'topology': {**descriptor['topology'], **workshop_topology(plan), 'warnings': ['Preview only. Creating a deployment records exclusive resource claims; preflight checks the selected target.'],
                'reservations': {'status': 'missing', 'issues': ['These assignments are not reserved until the deployment is created.']}}}
