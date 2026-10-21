"""Foundation reservations use the existing durable allocator without a public bypass."""
import httpx
import pytest
from sqlalchemy import select

from tests.routes import test_allocation_reservations as base_allocations
from tests.routes.test_allocation_reservations import HEADERS, TOKEN

from tests.core.test_workshop_foundations import context, specification


env = base_allocations.env

def request():
    return {'project_key': 'foundation-draft', 'vmid_start': 9240, 'vmid_end': 9241,
        'networks': [], 'vms': [{'node_id': 'foundation-proxmox', 'vm_id': 9240, 'nics': []},
                              {'node_id': 'foundation-deployer', 'vm_id': 9241, 'nics': []}]}


@pytest.mark.asyncio
async def test_networkless_guest_ids_use_normal_reservations_and_survive_retry(env):
    client, _, db, url = env
    payload = request()
    payload.update(vmid_start=45000, vmid_end=45001)
    for index, row in enumerate(payload['vms']):
        row['vm_id'] = 45000 + index
    first = await client.post(url, json=payload, headers=HEADERS)
    assert first.status_code == 200, first.text
    await db.dispose_engine()
    again = await client.post(url, json=payload, headers=HEADERS)
    assert again.status_code == 200 and again.json()['assignments'] == first.json()['assignments']
    assert again.json()['reservation_id'] == first.json()['reservation_id']
    assert all(not row['nics'] for row in again.json()['assignments'])


@pytest.mark.asyncio
async def test_plain_http_reservation_cannot_authorize_protected_template_ids(env):
    client, _, _, url = env
    response = await client.post(url, json=request(), headers=HEADERS)
    assert response.status_code == 409, response.text
    assert response.json()['code'] == 'ALLOCATION_PROTECTED'
    response = await client.post(url, json={**request(), 'approved_protected_vmids': [9240, 9241]}, headers=HEADERS)
    assert response.status_code == 422


async def selected(env):
    from app.core.models import ProxmoxHost
    from app.core.workshop_foundations import FoundationSpec, allocation_authority
    _, _, db, url = env
    async with db.get_session_factory()() as session:
        host = await session.get(ProxmoxHost, url.split('/')[4])
    selected_context = context()
    selected_context.target_host_id, selected_context.target_node = host.id, host.node_name
    selected_context.target_api_url = host.api_url
    return host, allocation_authority(FoundationSpec.model_validate(specification()), selected_context, host, run_id='b'*16)


@pytest.mark.asyncio
async def test_server_foundation_authority_uses_same_ledger_and_keeps_global_protection(env):
    from app.core.allocation_reservations import reserve
    from app.schemas.v1.allocation import AllocationRequest
    from app.core.allocation_models import AllocationReservation
    client, _, db, url = env
    host, authority = await selected(env)
    payload = AllocationRequest.model_validate(request())
    async with httpx.AsyncClient() as pve:
        lease = await reserve(db.get_session_factory(), host, payload, TOKEN, pve, template_authority=authority)
    assert [row.vm_id for row in lease.assignments] == [9240, 9241]
    response = await client.post(url, json={**request(), 'project_key': 'another'}, headers=HEADERS)
    assert response.status_code == 409 and response.json()['code'] == 'ALLOCATION_PROTECTED'
    async with db.get_session_factory()() as session:
        assert len((await session.scalars(select(AllocationReservation))).all()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['another_id', 'another_node', 'credential', 'host_override'])
async def test_foundation_exception_cannot_widen_the_reviewed_scope(env, damage):
    from app.core.allocation_reservations import reserve
    from app.core.errors import Range42Error
    from app.schemas.v1.allocation import AllocationRequest
    _, pve, db, _ = env
    host, authority = await selected(env)
    payload = request()
    if damage == 'another_id':
        payload['vms'][0]['vm_id'] = 9230
    if damage == 'another_node':
        host.node_name = 'foreign'
    if damage == 'credential':
        host.token_ref = 'changed@pve!token=changed'
    if damage == 'host_override':
        host.protected_vmids_override_json = '[[9240,9241]]'
    with pytest.raises(Range42Error):
        async with httpx.AsyncClient() as connection:
            await reserve(db.get_session_factory(), host, AllocationRequest.model_validate(payload), TOKEN,
                          connection, template_authority=authority)
    assert pve.probes == []
