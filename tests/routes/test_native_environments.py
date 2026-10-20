"""An administrator registers reusable native bounds without editing server files."""
import json

from httpx import ASGITransport, AsyncClient
import pytest

from tests.core.test_native_contexts import context
from tests.routes.test_deployments_preflight import _boot, _seed


def profile():
    return {'version': 1, 'id': 'training-runs', 'vmid_ranges': [[45000, 45039]],
        'networks': [{'bridge': 'vmbr600', 'subnet': '10.79.0.0/28', 'gateway': '10.79.0.1'}],
        'proxmox_template': 9240, 'deployer_template': 9241, 'storage': 'ceph-range'}


@pytest.fixture
async def environment(tmp_path, monkeypatch):
    workspace, script, _ = context(tmp_path, monkeypatch)
    registry = tmp_path / 'contexts.json'
    registry.write_text(json.dumps({'version': 1, 'contexts': [{'id': 'lab-demo', 'label': 'Trusted native runtime',
        'workspace': str(workspace), 'context_script': str(script), 'target_host_id': 'h',
        'target_node': 'n', 'target_api_url': 'https://192.0.2.10:8006'}]}))
    monkeypatch.setenv('RANGE42_NATIVE_CONTEXTS_FILE', str(registry))
    app, db = await _boot(tmp_path, monkeypatch)
    await _seed(db, tmp_path)
    from app.core.models import ProxmoxHost
    async with db.get_session_factory()() as session:
        host = await session.get(ProxmoxHost, 'h')
        host.api_url = 'https://192.0.2.10:8006'
        host.token_ref = 'read-old@pve!old=OLD-PRIVATE-TOKEN'
        session.add(ProxmoxHost(id='workshops', name='Scoped workshop provisioner', api_url=host.api_url,
            node_name='n', token_ref='workshops@pve!scoped=NEW-PRIVATE-TOKEN'))
        await session.commit()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://t') as client:
            yield client, db, workspace, registry
    finally:
        await db.dispose_engine()


def selection(**updates):
    return {'label': 'Independent training environments', 'base_context_id': 'lab-demo',
        'target_host_id': 'workshops', 'workshop_profile': profile(), **updates}


@pytest.mark.asyncio
async def test_review_register_and_resolve_reusable_profile_without_mutating_base(environment):
    client, db, workspace, registry = environment
    before = {p: p.read_bytes() for p in [registry, workspace / 'sourced_range42.sh', workspace / 'inventory/inventory_default.yml', workspace / 'secrets/default_vault.yml']}
    response = await client.post('/v1/environments/review', json=selection())
    assert response.status_code == 200, response.text
    review = response.json()
    assert review['ready_for_planning'] is True
    assert review['infrastructure_qualified'] is False
    assert review['workshop_profile'] == {**profile()}
    assert 'PRIVATE-TOKEN' not in response.text
    assert str(workspace) not in response.text
    created = await client.post('/v1/environments', json={**selection(), 'review_revision': review['revision']})
    assert created.status_code == 201, created.text
    ident = created.json()['id']
    contexts = (await client.get('/v1/contexts')).json()['items']
    old = next(row for row in contexts if row['id'] == 'lab-demo')
    new = next(row for row in contexts if row['id'] == ident)
    assert old['target_host_id'] == 'h' and old['workshop_profile'] is None
    assert new['target_host_id'] == 'workshops' and new['workshop_profile']['id'] == 'training-runs'
    assert new['ready'] is True
    from app.core.native_environments import resolve_registered_context
    from app.core.models import ProxmoxHost
    async with db.get_session_factory()() as session:
        resolved = await resolve_registered_context(session, ident, await session.get(ProxmoxHost, 'workshops'))
        assert resolved.provisioning_credential == 'workshops@pve!scoped=NEW-PRIVATE-TOKEN'
        assert resolved.workspace == workspace
    assert all(p.read_bytes() == body for p, body in before.items())
    assert (await client.get('/v1/environments')).json()['items'][0]['id'] == ident


@pytest.mark.asyncio
async def test_review_becomes_stale_on_target_credential_rotation_and_can_be_revalidated(environment):
    client, db, _, _ = environment
    revision = (await client.post('/v1/environments/review', json=selection())).json()['revision']
    from app.core.models import ProxmoxHost
    async with db.get_session_factory()() as session:
        (await session.get(ProxmoxHost, 'workshops')).token_ref = 'workshops@pve!scoped=ROTATED-PRIVATE-TOKEN'
        await session.commit()
    refused = await client.post('/v1/environments', json={**selection(), 'review_revision': revision})
    assert refused.status_code == 409
    revision = (await client.post('/v1/environments/review', json=selection())).json()['revision']
    created = await client.post('/v1/environments', json={**selection(), 'review_revision': revision})
    ident = created.json()['id']
    async with db.get_session_factory()() as session:
        (await session.get(ProxmoxHost, 'workshops')).token_ref = 'workshops@pve!scoped=SECOND-ROTATED-PRIVATE-TOKEN'
        await session.commit()
    contexts = (await client.get('/v1/contexts')).json()['items']
    assert next(row for row in contexts if row['id'] == ident)['ready'] is False
    from app.core.native_environments import resolve_registered_context
    from app.core.errors import Range42Error
    async with db.get_session_factory()() as session:
        with pytest.raises(Range42Error):
            await resolve_registered_context(session, ident, await session.get(ProxmoxHost, 'workshops'))
    review = (await client.post(f'/v1/environments/{ident}/review')).json()
    updated = await client.post(f'/v1/environments/{ident}/revalidate', json={'review_revision': review['revision']})
    assert updated.status_code == 200, updated.text
    assert updated.json()['id'] == ident


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['node', 'url', 'runtime', 'protected_vmids'])
async def test_stale_review_never_accepts_target_or_runtime_drift(environment, change):
    client, db, workspace, _ = environment
    revision = (await client.post('/v1/environments/review', json=selection())).json()['revision']
    from app.core.models import ProxmoxHost
    if change == 'runtime':
        (workspace / 'sourced_range42.sh').write_text('export RANGE42_INFRASTRUCTURE_CODENAME="replacement"\n')
    else:
        async with db.get_session_factory()() as session:
            host = await session.get(ProxmoxHost, 'workshops')
            if change == 'node':
                host.node_name = 'another-node'
            elif change == 'url':
                host.api_url = 'https://192.0.2.99:8006'
            else:
                host.protected_vmids_override_json = '[[45000,45039]]'
            await session.commit()
    refused = await client.post('/v1/environments', json={**selection(), 'review_revision': revision})
    assert refused.status_code == 409, refused.text
    assert (await client.get('/v1/environments')).json()['items'] == []


def test_provisioning_environment_management_is_administrator_only():
    from app.core.access import Principal, allowed
    for method, path in [('GET', '/v1/environments'), ('POST', '/v1/environments'),
            ('POST', '/v1/environments/review'), ('POST', '/v1/environments/infrastructure/review'), ('POST', '/v1/environments/{environment_id}/review'),
            ('POST', '/v1/environments/{environment_id}/revalidate')]:
        assert allowed(Principal('administrator', 'admin'), method, path)
        assert not allowed(Principal('facilitator', 'operator'), method, path)
        assert not allowed(Principal('observer', 'viewer'), method, path)


@pytest.mark.asyncio
async def test_registration_cannot_supply_runtime_paths_or_skip_review(environment):
    client, _, _, _ = environment
    for body in [selection(), {**selection(), 'workspace': '/etc'}, {**selection(), 'context_script': '/bin/sh'},
            {**selection(), 'execution_ready': True}, {**selection(), 'infrastructure_qualified': True}]:
        result = await client.post('/v1/environments', json=body)
        assert result.status_code == 422
    response = await client.post('/v1/environments/review', json=selection(base_context_id='unknown'))
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_environment_versions_cannot_be_retargeted_or_overwritten(environment):
    client, db, _, _ = environment
    revision = (await client.post('/v1/environments/review', json=selection())).json()['revision']
    created = await client.post('/v1/environments', json={**selection(), 'review_revision': revision})
    ident = created.json()['id']
    for method in ('put', 'patch', 'delete'):
        assert (await getattr(client, method)(f'/v1/environments/{ident}')).status_code in (404, 405)
    from app.core.models import ProxmoxHost
    async with db.get_session_factory()() as session:
        (await session.get(ProxmoxHost, 'workshops')).node_name = 'another-node'
        await session.commit()
    assert (await client.post(f'/v1/environments/{ident}/review')).status_code == 409
    assert (await client.post(f'/v1/environments/{ident}/revalidate', json={'review_revision': revision})).status_code == 409


@pytest.mark.asyncio
async def test_registered_environment_keeps_its_host_binding_on_host_delete(environment):
    client, _, _, _ = environment
    revision = (await client.post('/v1/environments/review', json=selection())).json()['revision']
    created = await client.post('/v1/environments', json={**selection(), 'review_revision': revision})
    response = await client.delete('/v1/proxmox/hosts/workshops')
    assert response.status_code == 409, response.text
    assert response.json()['code'] == 'HOST_IN_USE'
    assert (await client.get('/v1/environments')).json()['items'][0]['id'] == created.json()['id']


@pytest.mark.asyncio
async def test_gateway_binding_is_observed_and_rechecked_without_exposing_guest_secrets(environment, monkeypatch):
    import httpx
    client, _, _, _ = environment
    generation = '12345678-1234-1234-1234-123456789abc'
    data = {'vmgenid': generation, 'description': 'platform:training-gateway',
        'net0': 'virtio=02:00:00:00:00:01,bridge=vmbr429',
        'net1': 'virtio=02:00:00:00:00:02,bridge=vmbr600',
        'cipassword': 'NEVER-RETURN-GUEST-SECRET'}
    original = httpx.AsyncClient
    seen = []
    def handler(request):
        seen.append((request.url.path, dict(request.url.params)))
        assert request.headers['Authorization'] == 'PVEAPIToken=workshops@pve!scoped=NEW-PRIVATE-TOKEN'
        return httpx.Response(200, json={'data': dict(data)})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(handler)))
    response = await client.post('/v1/environments/infrastructure/review', json={**selection(), 'vmids': [42130]})
    assert response.status_code == 200, response.text
    observed = response.json()['guests']
    assert observed == [{'vmid': 42130, 'node': 'n', 'vmgenid': generation, 'owner': 'platform:training-gateway',
        'nics': [{'interface': 'net1', 'bridge': 'vmbr600', 'mac': '02:00:00:00:00:02'}]}]
    assert 'NEVER-RETURN' not in response.text and 'PRIVATE-TOKEN' not in response.text
    assert {item[1]['current'] for item in seen} == {'0', '1'}
    selected = selection(workshop_profile={**profile(), 'infrastructure_guests': observed})
    review = await client.post('/v1/environments/review', json=selected)
    assert review.status_code == 200, review.text
    data['vmgenid'] = 'aaaaaaaa-1234-1234-1234-123456789abc'
    refused = await client.post('/v1/environments', json={**selected, 'review_revision': review.json()['revision']})
    assert refused.status_code == 409, refused.text
    assert (await client.get('/v1/environments')).json()['items'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['pending', 'missing_owner', 'template', 'foreign_bridge', 'http_failure'])
async def test_infrastructure_review_rejects_incomplete_or_changed_gateway_proof(environment, monkeypatch, change):
    import httpx
    client, _, _, _ = environment
    original = httpx.AsyncClient
    def handler(request):
        data = {'vmgenid': '12345678-1234-1234-1234-123456789abc', 'description': 'platform:gateway',
            'net1': 'virtio=02:00:00:00:00:02,bridge=vmbr600'}
        if change == 'pending' and request.url.params['current'] == '0':
            data['net1'] = 'virtio=02:00:00:00:00:03,bridge=vmbr600'
        elif change == 'missing_owner':
            data['description'] = ''
        elif change == 'template':
            data['template'] = 1
        elif change == 'foreign_bridge':
            data['net1'] = 'virtio=02:00:00:00:00:02,bridge=vmbr999'
        return httpx.Response(403 if change == 'http_failure' else 200, json={'data': data})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(handler)))
    response = await client.post('/v1/environments/infrastructure/review', json={**selection(), 'vmids': [42130]})
    assert response.status_code in (409, 502), response.text


@pytest.mark.asyncio
async def test_credential_revalidation_preserves_cleanup_authority_when_gateway_is_offline(environment, monkeypatch):
    import httpx
    from app.core.models import ProxmoxHost
    client, db, _, _ = environment
    original = httpx.AsyncClient
    online = True
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200 if online else 503, json={'data': {
            'vmgenid': '12345678-1234-1234-1234-123456789abc', 'description': 'platform:gateway',
            'net1': 'virtio=02:00:00:00:00:02,bridge=vmbr600'}})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(handler)))
    observed = (await client.post('/v1/environments/infrastructure/review', json={**selection(), 'vmids': [42130]})).json()['guests']
    selected = selection(workshop_profile={**profile(), 'infrastructure_guests': observed})
    revision = (await client.post('/v1/environments/review', json=selected)).json()['revision']
    saved = await client.post('/v1/environments', json={**selected, 'review_revision': revision})
    assert saved.status_code == 201, saved.text
    identity = saved.json()['id']
    online = False
    calls.clear()
    async with db.get_session_factory()() as session:
        (await session.get(ProxmoxHost, 'workshops')).token_ref = 'workshops@pve!scoped=ROTATED-FIXTURE'
        await session.commit()
    review = await client.post(f'/v1/environments/{identity}/review')
    assert review.status_code == 200, review.text
    updated = await client.post(f'/v1/environments/{identity}/revalidate', json={'review_revision': review.json()['revision']})
    assert updated.status_code == 200, updated.text
    assert updated.json()['infrastructure_qualified'] is False
    assert calls == []


@pytest.mark.asyncio
async def test_installer_bound_workspace_supports_settings_review_without_registry_edits(environment, monkeypatch):
    client, _, workspace, _ = environment
    monkeypatch.delenv('RANGE42_NATIVE_CONTEXTS_FILE')
    (workspace / '.range42-target.json').write_text(json.dumps({'version': 1,
        'target_host_id': 'h', 'target_node': 'n', 'target_api_url': 'https://192.0.2.10:8006'}))
    bases = (await client.get('/v1/environments')).json()['base_contexts']
    assert len(bases) == 1 and bases[0]['ready'] is True
    assert bases[0]['id'] == 'lab-demo'
    review = await client.post('/v1/environments/review', json=selection())
    assert review.status_code == 200, review.text
    created = await client.post('/v1/environments', json={**selection(), 'review_revision': review.json()['revision']})
    assert created.status_code == 201, created.text
    assert created.json()['infrastructure_qualified'] is False


@pytest.mark.asyncio
async def test_explicit_target_rotation_requires_environment_revalidation_without_changing_bounds(environment):
    client, _, _, _ = environment
    chosen = selection()
    reviewed = (await client.post('/v1/environments/review', json=chosen)).json()
    created = await client.post('/v1/environments', json={**chosen, 'review_revision': reviewed['revision']})
    ident = created.json()['id']
    review = (await client.post('/v1/proxmox/hosts/workshops/credential/review')).json()
    rotated = await client.put('/v1/proxmox/hosts/workshops/credential', json={
        'expected_revision': review['revision'], 'token_ref': 'workshops@pve!scoped=ROTATED-PRIVATE-TOKEN'})
    assert rotated.status_code == 200, rotated.text
    item, = (await client.get('/v1/environments')).json()['items']
    assert item['id'] == ident and not item['ready_for_planning']
    assert item['workshop_profile'] == created.json()['workshop_profile']
    reviewed = (await client.post(f'/v1/environments/{ident}/review')).json()
    accepted = await client.post(f'/v1/environments/{ident}/revalidate', json={'review_revision': reviewed['revision']})
    assert accepted.status_code == 200 and accepted.json()['ready_for_planning']
    assert accepted.json()['workshop_profile'] == created.json()['workshop_profile']
    assert accepted.json()['infrastructure_qualified'] is False
