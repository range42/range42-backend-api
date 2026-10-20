"""A retained gateway can be reviewed before its future participant NICs exist."""
import httpx
import pytest

from tests.routes.test_native_environments import environment as environment, selection

GENERATION = '12345678-1234-1234-1234-123456789abc'


@pytest.fixture
async def gateway(environment, monkeypatch):
    client, db, _, _ = environment
    data = {'name': 'retained-gateway', 'vmgenid': GENERATION, 'description': 'platform:gateway',
        'net0': 'virtio=02:00:00:00:00:01,bridge=vmbr429', 'cipassword': 'GUEST-SECRET-MUST-NOT-RETURN'}
    original = httpx.AsyncClient
    seen = []
    def handler(request):
        seen.append((request.url.path, dict(request.url.params)))
        value = dict(data)
        if value.pop('_pending', False) and request.url.params.get('current') == '0':
            value['net0'] = 'virtio=02:00:00:00:00:02,bridge=vmbr429'
        return httpx.Response(200, json={'data': value})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(handler)))
    return client, db, data, seen


@pytest.mark.asyncio
async def test_review_selects_full_gateway_generation_before_new_ports_exist(gateway):
    client, _, _, seen = gateway
    response = await client.post('/v1/environments/foundation/review', json={**selection(), 'vmid': 42130})
    assert response.status_code == 200, response.text
    value = response.json()
    foundation = value['gateway_foundation']
    assert foundation['vmid'] == 42130 and foundation['vmgenid'] == GENERATION
    assert foundation['owner'] == 'platform:gateway' and foundation['node'] == 'n'
    assert foundation['nics'] == [{'interface': 'net0', 'bridge': 'vmbr429', 'mac': '02:00:00:00:00:01'}]
    assert len(foundation['configuration_revision']) == 64
    assert value['nic_capacity'] == {'limit': 32, 'occupied': 1, 'additional_required': 1, 'available': 31, 'remaining_after_preparation': 30}
    assert value['infrastructure_qualified'] is False
    assert {params['current'] for _, params in seen} == {'0', '1'}
    assert 'GUEST-SECRET' not in response.text and 'PRIVATE-TOKEN' not in response.text


@pytest.mark.asyncio
async def test_review_register_and_reopen_preserves_desired_bounds_and_planning_only_state(gateway):
    from app.core.native_environment_models import NativeEnvironment
    from app.core.native_environments import resolve_registered_context
    from app.core.models import ProxmoxHost
    client, db, _, _ = gateway
    foundation = (await client.post('/v1/environments/foundation/review', json={**selection(), 'vmid': 42130})).json().get('gateway_foundation')
    assert foundation is not None, 'Gateway foundation review is missing'
    desired = selection(gateway_foundation=foundation)
    review = await client.post('/v1/environments/review', json=desired)
    assert review.status_code == 200, review.text
    created = await client.post('/v1/environments', json={**desired, 'review_revision': review.json()['revision']})
    assert created.status_code == 201, created.text
    identifier = created.json()['id']
    assert created.json()['gateway_foundation'] == foundation
    assert created.json()['workshop_profile'] == selection()['workshop_profile']
    async with db.get_session_factory()() as session:
        stored = await session.get(NativeEnvironment, identifier)
        assert stored.foundation_json
        resolved = await resolve_registered_context(session, identifier, await session.get(ProxmoxHost, 'workshops'))
        assert resolved.execution_ready is False
        assert resolved.workshop_profile.infrastructure_guests == []
    row = (await client.get('/v1/environments')).json()['items'][0]
    assert row['gateway_foundation'] == foundation and row['ready_for_planning']
    assert row['gateway_nic_capacity']['remaining_after_preparation'] == 30
    assert row['infrastructure_qualified'] is False


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['generation', 'owner', 'nic', 'other_config'])
async def test_gateway_cannot_drift_between_review_and_registration(gateway, change):
    client, _, data, _ = gateway
    foundation = (await client.post('/v1/environments/foundation/review', json={**selection(), 'vmid': 42130})).json().get('gateway_foundation')
    assert foundation is not None, 'Gateway foundation review is missing'
    desired = selection(gateway_foundation=foundation)
    review = await client.post('/v1/environments/review', json=desired)
    assert review.status_code == 200, review.text
    if change == 'generation':
        data['vmgenid'] = 'aaaaaaaa-1234-1234-1234-123456789abc'
    elif change == 'owner':
        data['description'] = 'another:owner'
    elif change == 'nic':
        data['net0'] = 'virtio=02:00:00:00:00:02,bridge=vmbr429'
    else:
        data['cores'] = 8
    refused = await client.post('/v1/environments', json={**desired, 'review_revision': review.json()['revision']})
    assert refused.status_code == 409, refused.text
    assert (await client.get('/v1/environments')).json()['items'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['pending', 'template', 'missing_owner', 'extra_nic', 'duplicate_mac', 'malformed_nic', 'slot_limit'])
async def test_incomplete_gateway_or_insufficient_nic_capacity_is_refused(gateway, change):
    client, _, data, _ = gateway
    if change == 'pending':
        data['_pending'] = True
    elif change == 'template':
        data['template'] = 1
    elif change == 'missing_owner':
        data['description'] = ''
    elif change == 'extra_nic':
        data['net32'] = 'virtio=02:00:00:00:00:32,bridge=vmbr499'
    elif change == 'duplicate_mac':
        data['net1'] = data['net0'].replace('vmbr429', 'vmbr420')
    elif change == 'malformed_nic':
        data['net0'] = 'virtio=not-a-mac,bridge=vmbr429'
    else:
        data.update({f'net{index}': f'virtio=02:00:00:00:01:{index:02x},bridge=vmbr{400 + index}' for index in range(32)})
    response = await client.post('/v1/environments/foundation/review', json={**selection(), 'vmid': 42130})
    assert response.status_code == 409, response.text
    if change == 'slot_limit':
        assert response.json()['code'] == 'NATIVE_ENVIRONMENT_GATEWAY_CAPACITY'
    assert 'GUEST-SECRET' not in response.text


def test_gateway_foundation_review_stays_admin_only():
    from app.core.access import Principal, allowed
    path = '/v1/environments/foundation/review'
    assert allowed(Principal('admin', 'admin'), 'POST', path)
    assert not allowed(Principal('operator', 'operator'), 'POST', path)


@pytest.mark.asyncio
@pytest.mark.parametrize('vmid', [45000, 9240, 9241])
async def test_retained_gateway_is_separate_from_run_guests_and_templates(gateway, vmid):
    client, _, _, seen = gateway
    response = await client.post('/v1/environments/foundation/review', json={**selection(), 'vmid': vmid})
    assert response.status_code == 422, response.text
    assert not seen, 'Invalid gateway allocation should be refused before Proxmox inspection'


@pytest.mark.asyncio
async def test_repeated_and_concurrent_saves_of_one_review_preserve_one_environment(gateway):
    import asyncio
    client, _, _, _ = gateway
    inspected = await client.post('/v1/environments/foundation/review', json={**selection(), 'vmid': 42130})
    desired = selection(gateway_foundation=inspected.json()['gateway_foundation'])
    reviewed = await client.post('/v1/environments/review', json=desired)
    payload = {**desired, 'review_revision': reviewed.json()['revision']}
    pair = await asyncio.gather(*(client.post('/v1/environments', json=payload) for _ in range(2)))
    assert all(response.status_code == 201 for response in pair)
    assert pair[0].json()['id'] == pair[1].json()['id']
    first = pair[0]
    repeated = await client.post('/v1/environments', json=payload)
    assert repeated.status_code == 201
    assert repeated.json()['id'] == first.json()['id'], 'A lost save response must not create another environment on retry'
    concurrent = await asyncio.gather(*(client.post('/v1/environments', json=payload) for _ in range(2)))
    assert all(response.status_code == 201 for response in concurrent)
    assert {response.json()['id'] for response in concurrent} == {first.json()['id']}
    assert len((await client.get('/v1/environments')).json()['items']) == 1
