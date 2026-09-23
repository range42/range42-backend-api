"""SDN planning reads are credential-filtered and never mutate PVE."""
import httpx
import pytest
from fastapi import FastAPI

from app.core import db
from app.core.models import Base, ProxmoxHost
from app.core.errors import install_exception_handlers
from app.routes.v1.proxmox import router
from app.routes.v1.proxmox._helpers import _session


@pytest.fixture
async def fixture(tmp_path, monkeypatch):
    engine = db.build_engine(f"sqlite+aiosqlite:///{tmp_path / 'sdn.db'}")
    factory = db.session_factory(engine)
    monkeypatch.setattr(db, 'get_session_factory', lambda: factory)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        session.add(ProxmoxHost(id='host', name='fixture', api_url='https://pve.test:8006', node_name='pve01', token_ref='test!token=only'))
        await session.commit()
    bodies = {
        '/cluster/sdn/zones': [{'zone': 'beta', 'type': 'simple', 'nodes': 'pve01,pve02', 'pending': {'mtu': 1400}, 'state': 'changed', 'private': 'never-emit'}, {'zone': 'alpha', 'type': 'vlan'}],
        '/cluster/sdn/vnets': [{'vnet': 'training', 'zone': 'beta', 'type': 'vnet'}],
        '/cluster/sdn/vnets/training/subnets': [{'subnet': 'beta-10.20.30.0-24', 'vnet': 'training', 'gateway': '10.20.30.1', 'snat': 1}],
    }
    calls = []
    status = [200]
    def upstream(request):
        calls.append(request)
        assert request.method == 'GET'
        assert request.headers['Authorization'] == 'PVEAPIToken=test!token=only'
        return httpx.Response(status[0], json={'data': bodies[request.url.path.removeprefix('/api2/json')]})
    actual_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: actual_client(transport=httpx.MockTransport(upstream), **kwargs))
    app = FastAPI()
    app.include_router(router, prefix='/v1')
    install_exception_handlers(app)
    async def local_session():
        async with factory() as session:
            yield session
    app.dependency_overrides[_session] = local_session
    async with actual_client(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        yield client, bodies, calls, status
    await engine.dispose()


@pytest.mark.asyncio
async def test_zone_inventory_keeps_node_scope_and_pending_changes_with_bounded_pagination(fixture):
    client, bodies, calls, status = fixture
    response = await client.get('/v1/proxmox/hosts/host/sdn/zones?view=pending&offset=1&limit=1')
    assert response.status_code == 200
    body = response.json()
    assert body['total'] == 2 and body['visibility'] == 'credential_filtered'
    assert body['view'] == 'pending'
    assert body['items'] == [{'zone': 'beta', 'type': 'simple', 'nodes': ['pve01', 'pve02'], 'state': 'changed', 'has_pending': True}]
    assert dict(calls[0].url.params) == {'pending': '1'}
    assert 'never-emit' not in response.text


@pytest.mark.asyncio
async def test_vnet_and_subnet_reads_use_running_config_and_preserve_nat(fixture):
    client, bodies, calls, status = fixture
    assert (await client.get('/v1/proxmox/hosts/host/sdn/vnets')).json()['items'][0]['zone'] == 'beta'
    response = await client.get('/v1/proxmox/hosts/host/sdn/vnets/training/subnets')
    assert response.status_code == 200
    assert response.json()['items'][0] == {'subnet': 'beta-10.20.30.0-24', 'vnet': 'training', 'cidr': '10.20.30.0/24', 'gateway': '10.20.30.1', 'snat': True, 'state': None, 'has_pending': False}
    assert all(dict(call.url.params) == {'running': '1'} for call in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', [[{'zone': 'x'}], [{'zone': 'x', 'type': 'simple'}, {'zone': 'x', 'type': 'simple'}], {'zone': 'x'}, [{'zone': '../invalid', 'type': 'simple'}]])
async def test_malformed_zone_inventory_does_not_become_partial_success(fixture, bad):
    client, bodies, calls, status = fixture
    bodies['/cluster/sdn/zones'] = bad
    response = await client.get('/v1/proxmox/hosts/host/sdn/zones')
    assert response.status_code == 502
    assert response.json()['code'] == 'SDN_INVENTORY_UNAVAILABLE'


@pytest.mark.asyncio
async def test_upstream_denial_remains_safe_and_unknown_hosts_do_not_call_pve(fixture):
    client, bodies, calls, status = fixture
    assert (await client.get('/v1/proxmox/hosts/missing/sdn/zones')).status_code == 404
    assert not calls
    status[0] = 403
    response = await client.get('/v1/proxmox/hosts/host/sdn/zones')
    assert response.status_code == 502
    assert 'never-emit' not in response.text and 'test!token' not in response.text


@pytest.mark.asyncio
async def test_unsupported_views_and_traversal_are_refused_before_upstream(fixture):
    client, bodies, calls, status = fixture
    assert (await client.get('/v1/proxmox/hosts/host/sdn/zones?view=anything')).status_code == 422
    assert (await client.get('/v1/proxmox/hosts/host/sdn/vnets/bad.name/subnets')).status_code == 422
    assert not calls
