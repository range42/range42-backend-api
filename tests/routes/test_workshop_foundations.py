"""Catalog template preparation is a normal pinned deployment with durable ownership."""
import json

import pytest
from sqlalchemy import select

from tests.routes import test_workshop_runs as base_workshops

from tests.routes.test_allocation_reservations import HEADERS
from tests.core.test_workshop_foundations import specification
from app.core.capacity_ledger import assert_dispatch_accepted as real_dispatch


workshop_env = base_workshops.workshop_env

@pytest.fixture
async def foundation_env(workshop_env, tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.capacity_ledger.assert_dispatch_accepted", real_dispatch)
    client, db, repo = workshop_env
    registry = tmp_path/'registered-contexts.json'
    value = json.loads(registry.read_text())
    value['contexts'][0]['workshop_profile'].update(proxmox_template=9240, deployer_template=9241)
    registry.write_text(json.dumps(value))
    base = repo/'scenarios/installer-course/manifest'
    (base/'workshop.json').unlink()
    (base/'preparation.json').write_text(json.dumps({'version': 1, 'kind': 'native-workshop-foundation', 'title': 'Prepare templates'}))
    return client, db, repo


def preview_request():
    return {'sha': 'a'*40, 'path': 'scenarios/installer-course', 'context_id': 'lab-demo',
        'target_host_id': 'h', 'foundation': specification()}


def deployment_request():
    request = preview_request()
    return {'project_id': 'p', 'project_sha': request['sha'], 'codename': 'BASE',
        'scenario_label': 'workshop-foundation', 'target_host_id': request['target_host_id'], 'team_count': 1,
        'native': {key: request[key] for key in ('path', 'context_id', 'foundation')}}


@pytest.mark.asyncio
async def test_preview_and_reservation_derive_destinations_from_selected_profile(foundation_env):
    client, _, _ = foundation_env
    prefix = '/v1/projects/p/native-scenario/foundation'
    preview = await client.post(prefix+'/preview', json=preview_request())
    assert preview.status_code == 200, preview.text
    value = preview.json()
    assert value['resources_reserved'] is False and value['execution_ready'] is False
    assert {row['vm_id'] for row in value['topology']['vms']} == {9240, 9241}
    assert value['topology']['networks'] == []
    lease = await client.post(prefix+'/reservations', json={**preview_request(), 'project_key': 'foundation-owner'}, headers=HEADERS)
    assert lease.status_code == 200, lease.text
    assert [row['vm_id'] for row in lease.json()['assignments']] == [9240, 9241]
    raw = await client.post('/v1/proxmox/hosts/h/reservations', json={
        **value['allocation_request'], 'project_key': 'unscoped'}, headers=HEADERS)
    assert raw.status_code == 409 and raw.json()['code'] == 'ALLOCATION_PROTECTED'


@pytest.mark.asyncio
async def test_foundation_creation_consumes_same_core_lease_without_participant_credentials(foundation_env):
    from app.core.allocation_models import AllocationReservation, DeploymentAllocation
    from app.core.workshop_models import WorkshopRun
    client, db, _ = foundation_env
    prefix = '/v1/projects/p/native-scenario/foundation'
    reserved = await client.post(prefix+'/reservations', json={**preview_request(), 'project_key': 'foundation-owner'}, headers=HEADERS)
    assert reserved.status_code == 200, reserved.text
    response = await client.post('/v1/deployments/', json={**deployment_request(),
        'allocation_reservation_id': reserved.json()['reservation_id']}, headers=HEADERS)
    assert response.status_code == 201, response.text
    saved = response.json()
    assert saved['native']['foundation_plan']['environment_id'] == 'lab-demo'
    assert saved['native']['foundation_plan']['run_id'] == saved['id']
    await db.dispose_engine()
    async with db.get_session_factory()() as session:
        assert not list(await session.scalars(select(AllocationReservation)))
        claims = list(await session.scalars(select(DeploymentAllocation)))
        assert len(claims) == 1 and claims[0].deployment_id == saved['id']
        assert [row['vm_id'] for row in claims[0].assignments] == [9240, 9241]
        assert not list(await session.scalars(select(WorkshopRun)))
    again = await client.post(prefix+'/reservations', json={**preview_request(), 'project_key': 'other-foundation'}, headers=HEADERS)
    assert again.status_code == 409 and again.json()['code'] == 'ALLOCATION_OCCUPIED'
    start = await client.post(f"/v1/deployments/{saved['id']}/attempts", json={'scope': 'full'})
    assert start.status_code == 409, 'Unqualified foundation must not execute before native capacity/preparation acceptance'


@pytest.mark.asyncio
async def test_foundation_creation_without_the_reviewed_lease_is_refused(foundation_env):
    client, _, _ = foundation_env
    response = await client.post('/v1/deployments/', json=deployment_request())
    assert response.status_code == 409, response.text
    assert response.json()['code'] == 'FOUNDATION_RESERVATION_REQUIRED'


@pytest.mark.asyncio
async def test_wrong_owner_cannot_consume_foundation_reservation(foundation_env):
    from app.core.allocation_models import AllocationReservation, DeploymentAllocation
    client, db, _ = foundation_env
    reserved = await client.post('/v1/projects/p/native-scenario/foundation/reservations',
        json={**preview_request(), 'project_key': 'foundation-owner'}, headers=HEADERS)
    assert reserved.status_code == 200, reserved.text
    response = await client.post('/v1/deployments/', json={**deployment_request(),
        'allocation_reservation_id': reserved.json()['reservation_id']},
        headers={'X-Range42-Reservation-Token': 'foreign-owner-token-0123456789abcdef'})
    assert response.status_code == 403, response.text
    async with db.get_session_factory()() as session:
        assert len(list(await session.scalars(select(AllocationReservation)))) == 1
        assert not list(await session.scalars(select(DeploymentAllocation)))


async def created_foundation(foundation_env):
    client, db, _ = foundation_env
    reserved = await client.post('/v1/projects/p/native-scenario/foundation/reservations',
        json={**preview_request(), 'project_key': 'foundation-owner'}, headers=HEADERS)
    assert reserved.status_code == 200, reserved.text
    made = await client.post('/v1/deployments/', json={**deployment_request(),
        'allocation_reservation_id': reserved.json()['reservation_id']}, headers=HEADERS)
    assert made.status_code == 201, made.text
    return made.json(), client, db


@pytest.mark.asyncio
async def test_foundation_uses_existing_capacity_registry_and_original_native_request(foundation_env):
    from app.core import capacity_ledger as ledger, native_capacity
    from app.core.models import ProxmoxHost
    from app.core.physical_nodes import review_observation, save_observation, target_revision
    from tests.core.test_capacity_accounting import observation
    saved, client, db = await created_foundation(foundation_env)
    observed = observation()
    observed.node.node_name = 'n'
    observed.node.supported_operations = ['inspect', 'capacity', 'capacity-admit', 'capacity-status']
    factory = db.get_session_factory()
    async with factory() as session:
        host = await session.get(ProxmoxHost, 'h')
        reviewed = await review_observation(session, 'h', observed.node,
            agent_revision='f'*64, target_revision_at_observation=target_revision(host))
        await save_observation(session, reviewed, review_revision=reviewed.revision, actor='director')
    approved = await ledger.review(factory, saved['id'], observed, agent_revision='f'*64)
    assert approved.assessment['admissible']
    assert {row.vm_id for row in approved.candidate.guests} == {9240, 9241}
    assert sum(row.memory_mib for row in approved.candidate.guests) == 20480
    assert sum(backing.logical_bytes for row in approved.candidate.guests for backing in row.storage) == 192*2**30 + 8*2**20
    held = await ledger.reserve(factory, approved, actor='director')
    fresh = await ledger.review(factory, saved['id'], observed, agent_revision='f'*64)
    pending = await ledger.journal_native_request(factory, fresh, actor='director', expected_revision=held.approval_revision)
    request = native_capacity.request_for(pending, observed.node.installation_id)
    assert {row['vm_id'] for row in request['commitment']['guests']} == {9240, 9241}
    assert all(row['materialization'] is None for row in request['commitment']['guests'])
    from app.core.errors import Range42Error
    with pytest.raises(Range42Error) as failure:
        await ledger.retire_unused(factory, fresh, actor='director', expected_revision=fresh.revision)
    assert failure.value.code == 'CAPACITY_NATIVE_RECONCILIATION_REQUIRED'


@pytest.mark.asyncio
async def test_foundation_capacity_panel_is_applicable_before_native_execution(foundation_env):
    saved, client, _ = await created_foundation(foundation_env)
    response = await client.get(f"/v1/deployments/{saved['id']}/capacity")
    assert response.status_code == 200 and response.json()['applicable'] is True
    assert response.json()['execution_ready'] is False


async def accepted_foundation(foundation_env):
    from app.core import capacity_ledger as ledger, native_capacity
    from app.core.models import ProxmoxHost
    from app.core.physical_nodes import FOUNDATION_OPERATIONS, review_observation, save_observation, target_revision, digest
    from tests.core.test_capacity_accounting import observation
    saved, client, db = await created_foundation(foundation_env)
    observed = observation()
    observed.node.node_name = 'n'
    observed.node.supported_operations = list(FOUNDATION_OPERATIONS)
    factory = db.get_session_factory()
    async with factory() as session:
        host = await session.get(ProxmoxHost, 'h')
        reviewed = await review_observation(session, 'h', observed.node,
            agent_revision='f'*64, target_revision_at_observation=target_revision(host))
        await save_observation(session, reviewed, review_revision=reviewed.revision, actor='director')
    fresh = await ledger.review(factory, saved['id'], observed, agent_revision='f'*64)
    hold = await ledger.reserve(factory, fresh, actor='director')
    fresh = await ledger.review(factory, saved['id'], observed, agent_revision='f'*64)
    row = await ledger.journal_native_request(factory, fresh, actor='director', expected_revision=hold.approval_revision)
    request = native_capacity.request_for(row, observed.node.installation_id)
    proof = {'protocol': 'range42-native-capacity-receipt-v1', 'status': 'accepted', 'request_id': request['request_id'],
        'request_digest': digest(request), 'node': observed.node.model_dump(), 'receipt_revision': 1,
        'execution_ready': False, 'infrastructure_qualified': False}
    await native_capacity.record_acceptance(factory, fresh, request, proof)
    return saved, factory, fresh


@pytest.mark.asyncio
async def test_foundation_request_is_journaled_on_the_original_hold_before_any_root_dispatch(foundation_env):
    from app.core import foundation_execution, capacity_ledger
    from app.core.capacity_models import CapacityHold
    saved, factory, fresh = await accepted_foundation(foundation_env)
    value = await foundation_execution.journal(factory, fresh)
    assert value['protocol'] == 'range42-foundation-request-v1'
    assert value['plan'] == saved['native']['foundation_plan']
    assert {g['vm_id'] for g in value['capacity_request']['commitment']['guests']} == {9240, 9241}
    async with factory() as session:
        row = await session.scalar(select(CapacityHold))
        assert json.loads(row.preparation_request_json) == value
        assert row.preparation_receipt_json is None
        capacity_ledger.commitment(row)
    assert await foundation_execution.journal(factory, fresh) == value


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['request', 'plan', 'guest', 'receipt', 'target'])
async def test_foundation_journal_refuses_changed_original_ownership(foundation_env, damage):
    from app.core import foundation_execution
    from app.core.capacity_models import CapacityHold
    from app.core.models import Deployment, ProxmoxHost
    from app.core.errors import Range42Error
    saved, factory, fresh = await accepted_foundation(foundation_env)
    original = await foundation_execution.journal(factory, fresh)
    async with factory() as session:
        row = await session.scalar(select(CapacityHold))
        if damage == 'request':
            row.preparation_request_json = json.dumps({**original, 'command': 'id'})
        if damage == 'receipt':
            row.native_receipt_json = None
        if damage == 'guest':
            dep = await session.get(Deployment, saved['id'])
            dep.native = {**dep.native, 'foundation_plan': {**dep.native['foundation_plan'], 'templates': []}}
        if damage == 'plan':
            dep = await session.get(Deployment, saved['id'])
            dep.native = {**dep.native, 'foundation': {**dep.native['foundation'], 'namespace': 'different'}}
        if damage == 'target':
            host = await session.get(ProxmoxHost, 'h')
            host.token_ref = 'replacement!token=changed'
        await session.commit()
    with pytest.raises(Range42Error):
        await foundation_execution.journal(factory, fresh)


@pytest.mark.asyncio
async def test_only_a_journaled_foundation_can_open_its_bounded_preparation_dispatch(foundation_env, monkeypatch):
    from types import SimpleNamespace
    from app.core import foundation_execution, capacity_ledger
    from app.core.errors import Range42Error
    saved, factory, fresh = await accepted_foundation(foundation_env)
    monkeypatch.setattr('app.core.preparation_transport.agent_configuration', lambda *_: SimpleNamespace(revision='f'*64))
    async with factory() as session:
        with pytest.raises(Range42Error):
            await capacity_ledger.assert_dispatch_accepted(session, saved['id'])
    await foundation_execution.journal(factory, fresh)
    async with factory() as session:
        await capacity_ledger.assert_dispatch_accepted(session, saved['id'])
    # Changing the protected SSH service config must close the gate again.
    monkeypatch.setattr('app.core.preparation_transport.agent_configuration', lambda *_: SimpleNamespace(revision='a'*64))
    async with factory() as session:
        with pytest.raises(Range42Error):
            await capacity_ledger.assert_dispatch_accepted(session, saved['id'])


@pytest.mark.asyncio
async def test_foundation_runner_reconciles_first_and_sends_only_the_original_request(foundation_env):
    import copy
    from types import SimpleNamespace
    from app.core import foundation_execution
    saved, factory, fresh = await accepted_foundation(foundation_env)
    original = await foundation_execution.journal(factory, fresh)
    calls = []
    complete = {'protocol': 'range42-foundation-receipt-v1',
        'request_id': original['capacity_request']['request_id'],
        'request_digest': __import__('app.core.physical_nodes', fromlist=['digest']).digest(original),
        'status': 'complete', 'node': fresh.observed.node.model_dump(),
        'templates': [{'vm_id': spec['vm_id'], 'phase': 'complete',
            'vmgenid': f'aaaaaaaa-bbbb-4ccc-8ddd-{spec["vm_id"]:012d}', 'configuration_revision': str(index+1)*64,
            'volumes': [f'ceph-range:base-{spec["vm_id"]}-disk-0', f'ceph-range:vm-{spec["vm_id"]}-cloudinit']
                       + ([f'ceph-range:base-{spec["vm_id"]}-disk-1'] if index == 0 else [])}
            for index, spec in enumerate(original['plan']['templates'])],
        'foundation_ready': True, 'execution_ready': False, 'infrastructure_qualified': False}
    async def rpc(_config, **options):
        calls.append(options)
        value = copy.deepcopy(complete)
        if len(calls) == 1:
            value.update(status='absent', foundation_ready=False)
            for row in value['templates']:
                row.update(phase='pending', vmgenid=None, configuration_revision=None, volumes=[])
        return value
    result = await foundation_execution.run_original(SimpleNamespace(), original, rpc=rpc, poll_interval=0)
    assert result == complete
    assert [call['action'] for call in calls] == ['foundation-status', 'foundation-prepare', 'foundation-status']
    assert all(call['request'] == original for call in calls)


@pytest.mark.asyncio
async def test_foundation_overview_reviews_a_request_without_sending_a_vm_operation(foundation_env, monkeypatch):
    from types import SimpleNamespace
    saved, _, fresh = await accepted_foundation(foundation_env)
    client, _, _ = foundation_env
    from app.routes.v1.deployments import capacity
    async def observe(_deployment_id): return fresh
    monkeypatch.setattr(capacity, 'observe', observe)
    monkeypatch.setattr('app.core.preparation_transport.agent_configuration', lambda *_: SimpleNamespace(revision='f'*64))
    prefix = f'/v1/deployments/{saved["id"]}/foundation'
    before = await client.get(prefix)
    assert before.status_code == 200, before.text
    assert before.json()['request_recorded'] is False and before.json()['preparation_available'] is False
    after = await client.post(prefix+'/review', json={})
    assert after.status_code == 200, after.text
    assert after.json()['request_recorded'] is True and after.json()['preparation_available'] is True
    assert after.json()['execution_ready'] is False and after.json()['receipt'] is None
    extra = await client.post(prefix+'/review', json={'command': 'id'})
    assert extra.status_code == 422


@pytest.mark.asyncio
async def test_foundation_preflight_uses_its_exact_internal_allocation_and_node_capability(foundation_env, monkeypatch):
    from types import SimpleNamespace
    from app.core import foundation_execution
    from app.core.preflight import PreflightCheck
    saved, factory, fresh = await accepted_foundation(foundation_env)
    original = await foundation_execution.journal(factory, fresh)
    async def verified_inputs(_factory, deployment_id):
        assert deployment_id == saved['id']
        return original, SimpleNamespace(revision='f'*64)
    monkeypatch.setattr(foundation_execution, 'dispatch_inputs', verified_inputs)
    async def reachable(*_args): return PreflightCheck(check='proxmox_api', result='pass')
    monkeypatch.setattr('app.routes.v1.deployments.preflight.check_proxmox_api_status', reachable)
    client, _, _ = foundation_env
    response = await client.post(f'/v1/deployments/{saved["id"]}/preflight', json={'scope': 'full'})
    assert response.status_code == 200, response.text
    assert not [check for check in response.json()['checks'] if check['result'] == 'block'], response.text
    assert any(check['check'] == 'native_foundation' for check in response.json()['checks'])


@pytest.mark.asyncio
async def test_foundation_dispatch_does_not_authorize_general_vm_mutations(foundation_env, monkeypatch):
    from types import SimpleNamespace
    from app.core import foundation_execution, capacity_ledger
    from app.core.errors import Range42Error
    saved, factory, fresh = await accepted_foundation(foundation_env)
    monkeypatch.setattr('app.core.preparation_transport.agent_configuration', lambda *_: SimpleNamespace(revision='f'*64))
    await foundation_execution.journal(factory, fresh)
    async with factory() as session:
        await capacity_ledger.assert_dispatch_accepted(session, saved['id'])
        for vmid in [9240, 9241]:
            with pytest.raises(Range42Error) as error:
                await capacity_ledger.assert_vm_capacity_accepted(session, vmid)
            assert error.value.code == 'FOUNDATION_BOUNDED_OPERATION_REQUIRED'
