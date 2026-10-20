"""Owned generic workshops use normal native deployments and the shared ledger."""
import json
import shutil
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from tests.core.test_native_contexts import context
from tests.core.test_native_scenarios import scenario
from tests.core.test_workshop_runs import specification
from tests.routes.test_deployments_preflight import _boot, _seed


@pytest.fixture
async def workshop_env(tmp_path, monkeypatch):
    from app.core import config
    from app.core import scenario as scenario_module
    from app.routes.v1.deployments import crud
    from app.core.models import ProxmoxHost

    from app.core import allocation_reservations
    from app.core.allocation_occupancy import Occupancy
    async def occupancy(*args, **kwargs): return Occupancy()
    async def available(*args, **kwargs): return True
    monkeypatch.setattr(allocation_reservations, 'read_occupancy', occupancy)
    monkeypatch.setattr(allocation_reservations, 'vmid_is_free', available)
    installed = tmp_path / 'installed'
    (installed / 'scenarios').mkdir(parents=True)
    (installed / 'scenarios/_reserved.json').write_text('')
    monkeypatch.setenv('API_BACKEND_WWWAPP_PLAYBOOKS_DIR', str(installed))
    workspace, script, _ = context(tmp_path, monkeypatch)
    profile = {'version': 1, 'id': 'installation-workshops', 'vmid_ranges': [[45000, 46999]],
        'proxmox_template': 9230, 'deployer_template': 9222, 'storage': 'ceph-range',
        'networks': [{'bridge': f'vmbr{bridge + seat}', 'subnet': f'10.{octet}.0.{16 * seat}/28',
            'gateway': f'10.{octet}.0.{16 * seat + 1}'} for bridge, octet in [(500, 78), (600, 79)] for seat in range(10)]}
    registry = tmp_path / 'registered-contexts.json'
    registry.write_text(json.dumps({'version': 1, 'contexts': [{'id': 'lab-demo', 'workspace': str(workspace),
        'context_script': str(script), 'target_host_id': 'h', 'target_node': 'n',
        'target_api_url': 'https://192.0.2.10:8006', 'workshop_profile': profile}]}))
    monkeypatch.setenv('RANGE42_NATIVE_CONTEXTS_FILE', str(registry))
    repo = tmp_path / 'source'
    base = scenario(repo, 'scenarios/installer-course')
    (base / 'manifest/scenario_vms.json').write_text('{"version":2,"vms":[]}')
    (base / 'manifest/workshop.json').write_text(json.dumps({'version': 1,
        'kind': 'native-installation', 'title': 'Install Range42', 'min_seats': 1, 'max_seats': 10}))
    (base / 'configure.yml').write_text('- hosts: all\n  tasks: []\n')
    app, db = await _boot(tmp_path, monkeypatch)
    monkeypatch.setattr(crud, 'settings', config.settings)
    monkeypatch.setenv('RANGE42_AUTO_START_ATTEMPTS', '0')
    def checkout(**kwargs):
        shutil.copytree(repo, kwargs['dest'])
        return kwargs['dest']
    monkeypatch.setattr(scenario_module, 'checkout_repository', checkout)
    await _seed(db, tmp_path, repo_owner='range42', repo_name='playbooks')
    async with db.get_session_factory()() as session:
        host = await session.get(ProxmoxHost, 'h')
        host.api_url = 'https://192.0.2.10:8006'
        host.token_ref = 'workshop@pve!limited=FIXTURE-SELECTED-TOKEN'
        await session.commit()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://t') as client:
            yield client, db, repo
    finally:
        await db.dispose_engine()


def creation(codename='COURSE', **changes):
    return {'project_id': 'p', 'codename': codename, 'scenario_label': 'installer-course',
        'target_host_id': 'h', 'team_count': 1, 'project_sha': 'a' * 40,
        'native': {'path': 'scenarios/installer-course', 'context_id': 'lab-demo',
                   'workshop': specification(**changes)}}


@pytest.mark.asyncio
async def test_workshop_creation_requires_live_checked_client_owned_reservation(workshop_env):
    from app.core.models import Deployment
    from sqlalchemy import select
    client, db, _ = workshop_env
    response = await client.post('/v1/deployments/', json=creation())
    assert response.status_code == 409, response.text
    assert response.json()['code'] == 'WORKSHOP_RESERVATION_REQUIRED'
    async with db.get_session_factory()() as session:
        assert await session.scalar(select(Deployment.id).where(Deployment.codename == 'COURSE')) is None


@pytest.mark.asyncio
async def test_preview_supplies_exact_request_for_existing_resource_reservation_service(workshop_env):
    from app.schemas.v1.allocation import AllocationRequest
    client, _, _ = workshop_env
    payload = creation()
    response = await client.post('/v1/projects/p/native-scenario/preview', json={
        'sha': payload['project_sha'], 'path': payload['native']['path'],
        'workshop': payload['native']['workshop']})
    assert response.status_code == 200, response.text
    request = AllocationRequest.model_validate({**response.json()['allocation_request'], 'project_key': 'draft-123'})
    assert (request.vmid_start, request.vmid_end) == (45000, 45009)
    assert [vm.vm_id for vm in request.vms] == list(range(45000, 45010))
    assert len(request.networks) == 5
    assert request.networks[0].subnet == '10.78.0.0/28'
    assert request.vms[0].nics[0].ip == '10.78.0.2'


async def reserve_draft(client, payload):
    preview = await client.post('/v1/projects/p/native-scenario/preview', json={
        'sha': payload['project_sha'], 'path': payload['native']['path'],
        'workshop': payload['native']['workshop']})
    assert preview.status_code == 200, preview.text
    headers = {'X-Range42-Reservation-Token': uuid.uuid4().hex + uuid.uuid4().hex}
    lease = await client.post('/v1/proxmox/hosts/h/reservations',
        json={**preview.json()['allocation_request'], 'project_key': str(uuid.uuid4())}, headers=headers)
    return lease, headers


async def create_reserved(client, payload):
    lease, headers = await reserve_draft(client, payload)
    if lease.status_code != 200:
        return lease
    return await client.post('/v1/deployments/', json={**payload,
        'allocation_reservation_id': lease.json()['reservation_id']}, headers=headers)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['token', 'expired', 'mapping'])
async def test_workshop_reservation_handoff_rejects_forged_or_stale_proofs(workshop_env, fault):
    from app.core.allocation_models import AllocationReservation
    from datetime import datetime, timezone
    client, db, _ = workshop_env
    payload = creation()
    lease, headers = await reserve_draft(client, payload)
    assert lease.status_code == 200, lease.text
    if fault == 'token':
        headers['X-Range42-Reservation-Token'] = 'forged' * 10
    elif fault == 'mapping':
        payload['native']['workshop']['vmid_start'] += 100
    else:
        async with db.get_session_factory()() as session:
            (await session.get(AllocationReservation, lease.json()['reservation_id'])).expires_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
            await session.commit()
    result = await client.post('/v1/deployments/', json={**payload,
        'allocation_reservation_id': lease.json()['reservation_id']}, headers=headers)
    assert result.status_code in (403, 409), result.text
    async with db.get_session_factory()() as session:
        assert await session.scalar(text('SELECT count(*) FROM workshop_runs')) == 0


@pytest.mark.asyncio
async def test_occupied_live_vmid_cannot_be_reserved_or_stranded_in_a_workshop_claim(workshop_env, monkeypatch):
    from app.core import allocation_reservations
    from app.core.allocation_occupancy import Occupancy
    async def occupied(*args, **kwargs): return Occupancy(vmids={45000})
    monkeypatch.setattr(allocation_reservations, 'read_occupancy', occupied)
    client, db, _ = workshop_env
    result = await create_reserved(client, creation())
    assert result.status_code == 409, result.text
    assert result.json()['code'] == 'ALLOCATION_OCCUPIED'
    async with db.get_session_factory()() as session:
        assert await session.scalar(text('SELECT count(*) FROM deployment_allocations')) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['missing', 'foreign_bridge', 'foreign_subnet', 'foreign_template', 'foreign_vmids'])
async def test_workshop_creation_requires_a_server_approved_provisioning_profile(workshop_env, monkeypatch, fault):
    import os
    from pathlib import Path
    client, db, _ = workshop_env
    payload = creation()
    if fault == 'missing':
        registry = Path(os.environ['RANGE42_NATIVE_CONTEXTS_FILE'])
        saved = json.loads(registry.read_text())
        del saved['contexts'][0]['workshop_profile']
        registry.write_text(json.dumps(saved))
    elif fault == 'foreign_bridge':
        payload['native']['workshop']['bridge_start'] = 420
    elif fault == 'foreign_subnet':
        payload['native']['workshop']['network_pool'] = '10.77.0.0/24'
    elif fault == 'foreign_template':
        payload['native']['workshop']['proxmox_template'] = 9999
    else:
        payload['native']['workshop']['vmid_start'] = 48000
    result = await create_reserved(client, payload)
    assert result.status_code == 409, result.text
    assert result.json()['code'].startswith('WORKSHOP_PROVISIONING_')
    async with db.get_session_factory()() as session:
        assert await session.scalar(text('SELECT count(*) FROM workshop_runs')) == 0


@pytest.mark.asyncio
async def test_create_records_immutable_run_and_claims_without_installation_identity_provider(workshop_env):
    client, db, _ = workshop_env
    created = await create_reserved(client, creation())
    assert created.status_code == 201, created.text
    row = created.json()
    plan = row['native']['workshop_plan']
    assert plan['run_id'] == row['id']
    assert plan['identity_profile'] is None and plan['access_profile'] is None
    claimed = await client.get(f"/v1/deployments/{row['id']}/allocations")
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()['assignments'] == plan['assignments']
    roster = await client.get(f"/v1/deployments/{row['id']}/workshop")
    assert roster.status_code == 200, roster.text
    assert len(roster.json()['participants']) == 5
    assert roster.json()['phase'] == 'draft'
    assert roster.json()['identity']['mode'] == 'instance-local'
    assert roster.json()['access']['state'] == 'not_configured'
    assert 'password' not in roster.text and 'password' not in created.text
    async with db.get_session_factory()() as session:
        private = await session.scalar(text('SELECT credentials FROM workshop_runs'))
        assert private.startswith('range42:fernet:v1:')
    await db.dispose_engine()
    assert (await client.get(f"/v1/deployments/{row['id']}/workshop")).json() == roster.json()


@pytest.mark.asyncio
async def test_human_setup_preview_uses_the_same_compiler_as_execution(workshop_env):
    client, _, _ = workshop_env
    payload = creation()
    preview = await client.post('/v1/projects/p/native-scenario/preview', json={
        'sha': payload['project_sha'], 'path': payload['native']['path'],
        'workshop': payload['native']['workshop']})
    assert preview.status_code == 200, preview.text
    shown = preview.json()
    assert shown['preview_only'] is True and shown['resources_reserved'] is False
    assert shown['topology']['vms'][0]['vm_id'] == 45000
    assert len(shown['topology']['vms']) == 10
    assert len(shown['topology']['networks']) == 5
    assert shown['capacity']['memory_mib'] == 102400
    created = await create_reserved(client, payload)
    actual = created.json()['native']['workshop_plan']
    assert shown['assignments'] == actual['assignments']


@pytest.mark.asyncio
async def test_participant_recovery_is_an_ordinary_durable_native_attempt(workshop_env):
    client, _, _ = workshop_env
    created = await create_reserved(client, creation())
    ident = created.json()['id']
    base = f'/v1/deployments/{ident}/workshop/participants'
    for participant, body in [('user06', {'action': 'reset', 'confirmation': 'user06'}),
        ('user01', {'action': 'reset', 'confirmation': 'user02'}),
        ('user01', {'action': 'shell', 'confirmation': 'user01'})]:
        assert (await client.post(f'{base}/{participant}/actions', json=body)).status_code in (404, 409, 422)
    accepted = await client.post(f'{base}/user01/actions', json={'action': 'reset', 'confirmation': 'user01'})
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()['scope'] == 'reset'
    assert accepted.json()['operation'] == {'kind': 'workshop', 'seat': 1, 'username': 'user01'}
    attempts = (await client.get(f'/v1/deployments/{ident}/attempts')).json()['items']
    assert len(attempts) == 1 and attempts[0]['id'] == accepted.json()['id']
    another = await client.post(f'{base}/user02/actions', json={'action': 'configure'})
    assert another.status_code == 409  # Existing deployment lock owns the entire run.


@pytest.mark.asyncio
async def test_workshop_resets_cannot_accidentally_select_every_participant(workshop_env):
    client, _, _ = workshop_env
    created = await create_reserved(client, creation())
    result = await client.post(f"/v1/deployments/{created.json()['id']}/attempts",
        json={'scope': 'reset', 'confirm_codename': 'COURSE'})
    assert result.status_code == 409, result.text
    assert result.json()['code'] == 'WORKSHOP_SEAT_REQUIRED'


@pytest.mark.asyncio
async def test_private_handout_contains_only_this_runs_selected_participant(workshop_env):
    import io
    from pypdf import PdfReader
    from app.core.workshop_models import WorkshopRun
    from app.core.access import Principal, allowed
    client, db, _ = workshop_env
    created = await create_reserved(client, creation())
    ident = created.json()['id']
    async with db.get_session_factory()() as session:
        material = json.loads((await session.get(WorkshopRun, ident)).credentials)
    response = await client.get(f'/v1/deployments/{ident}/workshop/participants/user01/handout')
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['content-type'] == 'application/pdf'
    reader = PdfReader(io.BytesIO(response.content))
    text = '\n'.join(page.extract_text() for page in reader.pages)
    assert 'training-a' in text and '2030-10-23' in text
    assert material['user01']['proxmox_password'] in text
    assert material['user01']['deployer_password'] in text
    assert material['user02']['proxmox_password'] not in text
    assert '10.78.0.2' in text and '10.78.0.18' not in text
    assert 'Keycloak' not in text and 'Headscale' not in text and '23 October 2026' not in text
    assert not allowed(Principal('viewer', 'viewer'), 'GET', '/v1/deployments/{deployment_id}/workshop/participants/{username}/handout')
    assert not allowed(Principal('operator', 'operator'), 'GET', '/v1/deployments/{deployment_id}/workshop/participants/{username}/handout')
    assert (await client.get(f'/v1/deployments/{ident}/workshop/participants/user06/handout')).status_code == 404


@pytest.mark.asyncio
async def test_roster_reports_observed_participant_operations_without_claiming_network_access(workshop_env):
    from app.core.models import Attempt
    from datetime import datetime, timezone
    client, db, _ = workshop_env
    created = await create_reserved(client, creation())
    ident = created.json()['id']
    async with db.get_session_factory()() as session:
        session.add(Attempt(id='whole', deployment_id=ident, scope='full', state='succeeded', rc=0,
            started_at=datetime(2029, 1, 1, tzinfo=timezone.utc), ended_at=datetime(2029, 1, 1, tzinfo=timezone.utc)))
        session.add(Attempt(id='seat-reset', deployment_id=ident, scope='reset', state='failed',
            operation={'kind': 'workshop', 'seat': 1, 'username': 'user01'},
            started_at=datetime(2029, 1, 2, tzinfo=timezone.utc)))
        await session.commit()
    status = (await client.get(f'/v1/deployments/{ident}/workshop')).json()
    assert status['phase'] == 'partial'
    assert status['participants'][0]['state'] == 'failed'
    assert status['participants'][1]['state'] == 'prepared'
    assert status['participants'][0]['attempt_id'] == 'seat-reset'
    assert status['access']['state'] == 'not_configured'
    assert status['ready'] is False
    assert status['permissions'] == {'manage_instances': True, 'download_credentials': True}


@pytest.mark.asyncio
@pytest.mark.parametrize('changed_target', [False, True])
async def test_native_launch_rechecks_claims_and_redacts_run_local_credentials(workshop_env, monkeypatch, changed_target):
    from app.core import deploy_trigger
    from app.core.models import Attempt, ProxmoxHost
    from app.core.preflight import PreflightCheck
    from app.core.workshop_models import WorkshopRun
    from app.core.errors import Range42Error
    from pathlib import Path
    client, db, _ = workshop_env
    created = await create_reserved(client, creation())
    ident = created.json()['id']
    started = await client.post(f'/v1/deployments/{ident}/attempts', json={'scope': 'full'})
    assert started.status_code == 201, started.text
    observed = []
    async def prerequisites(plan, host, **kwargs):
        observed.append((plan['run_id'], host.id))
        return [PreflightCheck(check='workshop_test_fixture', result='pass')]
    monkeypatch.setattr('app.core.workshop_preflight.workshop_checks', prerequisites)
    monkeypatch.setattr(deploy_trigger, '_decrypt_vault', lambda *_: {})
    captured = {}
    class Handle:
        pid = None
        async def wait(self): return 0
        async def kill(self): pass
    class Runner:
        async def start(self, **kwargs):
            captured.update(kwargs)
            return Handle()
    async with db.get_session_factory()() as session:
        material = json.loads((await session.get(WorkshopRun, ident)).credentials)
        attempt = await session.get(Attempt, started.json()['id'])
        if changed_target:
            (await session.get(ProxmoxHost, 'h')).node_name = 'different-node'
            await session.commit()
            with pytest.raises(Range42Error, match='target|binding'):
                await deploy_trigger.start_attempt(session, attempt=attempt, runner=Runner())
            assert not captured
            return
        await deploy_trigger.start_attempt(session, attempt=attempt, runner=Runner())
    assert observed == [(ident, 'h')]
    directory = Path(captured['private_data_dir'])
    values = json.loads((directory / 'native-context/variables.json').read_text())
    assert values['range42_workshop_plan'] == created.json()['native']['workshop_plan']
    assert values['range42_workshop_participants'] == material
    assert values['proxmox_api_user'] == 'workshop@pve'
    assert values['proxmox_api_token_secret'] == 'FIXTURE-SELECTED-TOKEN'
    redacted_values = json.loads((directory / 'redaction.json').read_text())
    assert all(value in redacted_values for record in material.values() for value in record.values())
    assert 'FIXTURE-SELECTED-TOKEN' in redacted_values
    assert 'workshop@pve!limited=FIXTURE-SELECTED-TOKEN' in redacted_values


@pytest.mark.asyncio
async def test_overlapping_runs_cannot_claim_the_same_bridges_even_with_different_addresses(workshop_env):
    client, db, _ = workshop_env
    first = await create_reserved(client, creation())
    assert first.status_code == 201, first.text
    second = await create_reserved(client, creation('OTHER',
        namespace='other', vmid_start=46000, network_pool='10.79.0.0/24'))
    assert second.status_code == 409, second.text
    assert second.json()['code'] == 'ALLOCATION_NETWORK_OCCUPIED'
    async with db.get_session_factory()() as session:
        assert await session.scalar(text('SELECT count(*) FROM workshop_runs')) == 1
    independent = await create_reserved(client, creation('OTHER',
        namespace='other', vmid_start=46000, network_pool='10.79.0.0/24', bridge_start=600))
    assert independent.status_code == 201, independent.text
    assert independent.json()['native']['workshop_plan']['run_id'] != first.json()['id']


@pytest.mark.asyncio
async def test_concurrent_runs_cannot_reuse_a_dns_namespace_with_different_vmids(workshop_env):
    client, _, _ = workshop_env
    assert (await create_reserved(client, creation())).status_code == 201
    second = await create_reserved(client, creation('OTHER', vmid_start=46000,
        network_pool='10.79.0.0/24', bridge_start=600))
    assert second.status_code == 409, second.text
    assert second.json()['code'] == 'ALLOCATION_NAMESPACE_OCCUPIED'


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['expired', 'unknown_provider', 'forged_plan', 'missing_spec'])
async def test_invalid_workshop_is_rejected_before_persisting_a_run(workshop_env, change):
    client, db, _ = workshop_env
    payload = creation()
    if change == 'expired':
        payload['native']['workshop']['expires_at'] = '2020-01-01T00:00:00Z'
    elif change == 'unknown_provider':
        payload['native']['workshop']['identity_profile'] = 'unregistered-provider'
    elif change == 'forged_plan':
        payload['native']['parameters'] = {'WORKSHOP_RUN_PLAN': '{}'}
    else:
        del payload['native']['workshop']
    result = await client.post('/v1/deployments/', json=payload)
    assert result.status_code in (409, 422), result.text
    async with db.get_session_factory()() as session:
        assert await session.scalar(text('SELECT count(*) FROM workshop_runs')) == 0
