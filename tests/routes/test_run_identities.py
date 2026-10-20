"""Run onboarding uses backend-owned scope and optional provider-neutral identity runtime."""
import importlib
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

ROOT = Path(__file__).resolve().parents[2]
PERSON = 'person-' + '1' * 32


def module():
    assert (ROOT / 'app/routes/v1/deployments/identities.py').exists(), 'Missing deployment identity management'
    return importlib.import_module('app.routes.v1.deployments.identities')


def state():
    return {'registry_id': 'a' * 32, 'revision': 7,
        'people': [{'id': PERSON, 'kind': 'temporary', 'enabled': True, 'generation': 1, 'reconcile_state': 'pending'}],
        'scopes': [], 'source_references': [], 'providers': [], 'bindings': [], 'memberships': [], 'jobs': []}


@pytest.fixture
async def env(tmp_path, monkeypatch):
    mod = module()
    from tests.routes.test_deployments_preflight import _boot, _seed
    from app.core.models import Deployment
    app, db = await _boot(tmp_path, monkeypatch)
    await _seed(db, tmp_path)
    async with db.get_session_factory()() as session:
        session.add(Deployment(id='run-a', codename='COURSE', project_id='p', project_sha='a' * 40,
            scenario_label='course', target_host_id='h', workspace_path=str(tmp_path / 'run-a'), native={'workshop_plan': {
                'run_id': 'run-a', 'expires_at': '2030-01-02T00:00:00Z',
                'participants': [{'username': 'user01', 'seat': 1}]}}))
        await session.commit()
    calls = []
    async def runtime(value):
        calls.append(value)
        return state() if value['action'] == 'status' else {'result': {'id': PERSON}, 'state': state()}
    monkeypatch.setattr(mod, 'identity_request', runtime)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://t') as client:
            yield client, calls, db
    finally:
        await db.dispose_engine()


@pytest.mark.asyncio
async def test_read_review_then_register_uses_saved_run_scope_and_deadline(env):
    client, calls, _ = env
    response = await client.get('/v1/deployments/run-a/identities')
    assert response.status_code == 200, response.text
    review = response.json()
    assert review['deployment_id'] == 'run-a' and review['expires_at'] == '2030-01-02T00:00:00Z'
    assert review['teams'] == ['user01']
    response = await client.post('/v1/deployments/run-a/identities', json={
        'registry_id': review['state']['registry_id'], 'expected_revision': review['state']['revision'],
        'run_revision': review['run_revision'], 'operation': {'type': 'scope.register'}})
    assert response.status_code == 200, response.text
    assert calls[-1]['request']['operation'] == {'type': 'scope.register', 'scope_ref': 'run-a', 'expires_at': '2030-01-02T00:00:00Z', 'participant_limit_per_team': 1}
    assert calls[-1]['actor'] == 'development'


@pytest.mark.asyncio
async def test_client_cannot_override_actor_scope_deadline_or_claim_reconciliation(env):
    client, calls, _ = env
    review = (await client.get('/v1/deployments/run-a/identities')).json()
    baseline = len(calls)
    valid = {'registry_id': review['state']['registry_id'], 'expected_revision': review['state']['revision'],
             'run_revision': review['run_revision'], 'operation': {'type': 'scope.register'}}
    for extra in [
        {'actor': 'another-staff'},
        {'operation': {'type': 'scope.register', 'scope_ref': 'other-run'}},
        {'operation': {'type': 'scope.register', 'expires_at': '2099-01-01T00:00:00Z'}},
        {'operation': {'type': 'scope.register', 'participant_limit_per_team': 99}},
        {'operation': {'type': 'reconcile.succeeded', 'job_id': '123'}},
        {'operation': {'type': 'provider.bind', 'subject': 'another-person'}},
        {'operation': {'type': 'person.register', 'record_ref': 'user01', 'kind': 'temporary', 'password': 'DO-NOT-ECHO'}},
    ]:
        response = await client.post('/v1/deployments/run-a/identities', json=valid | extra)
        assert response.status_code == 422, response.text
        assert 'DO-NOT-ECHO' not in response.text
    assert len(calls) == baseline


@pytest.mark.asyncio
async def test_unknown_run_team_and_changed_review_never_reach_runtime(env):
    client, calls, _ = env
    review = (await client.get('/v1/deployments/run-a/identities')).json()
    baseline = len(calls)
    value = {'registry_id': review['state']['registry_id'], 'expected_revision': review['state']['revision'],
        'run_revision': review['run_revision'], 'operation': {'type': 'membership.set', 'person_id': PERSON, 'role': 'participant', 'team_ref': 'other-run-seat'}}
    response = await client.post('/v1/deployments/run-a/identities', json=value)
    assert response.status_code == 422
    value['operation']['team_ref'] = 'user01'
    value['run_revision'] = '0' * 64
    response = await client.post('/v1/deployments/run-a/identities', json=value)
    assert response.status_code == 409 and response.json()['code'] == 'IDENTITY_RUN_REVIEW_CHANGED'
    response = await client.get('/v1/deployments/missing/identities')
    assert response.status_code == 404
    assert len(calls) == baseline


def test_identity_read_and_write_are_admin_only():
    module()
    from app.core.access import Principal, allowed
    path = '/v1/deployments/{deployment_id}/identities'
    for role in ['viewer', 'operator']:
        assert not allowed(Principal('staff', role), 'GET', path)
        assert not allowed(Principal('staff', role), 'POST', path)
    assert allowed(Principal('admin', 'admin'), 'GET', path)


@pytest.mark.asyncio
async def test_projection_removes_unexpected_provider_secrets_and_prevents_caching(env, monkeypatch):
    client, _, _ = env
    async def runtime(value):
        return {**state(), 'password': 'SECRET-DO-NOT-ECHO', 'people': [
            {**state()['people'][0], 'client_secret': 'SECRET-DO-NOT-ECHO'}]}
    monkeypatch.setattr(module(), 'identity_request', runtime)
    response = await client.get('/v1/deployments/run-a/identities')
    assert response.status_code == 200
    assert 'SECRET-DO-NOT-ECHO' not in response.text
    assert response.headers['cache-control'] == 'no-store'


@pytest.mark.asyncio
async def test_real_platform_dispatcher_runs_scope_roster_membership_and_removal(env, tmp_path, monkeypatch):
    import asyncio
    import os
    import sys
    source = os.environ.get('RANGE42_IDENTITY_RUNTIME_SOURCE')
    if not source:
        pytest.skip('Set RANGE42_IDENTITY_RUNTIME_SOURCE for the private runtime wire contract')
    assert (Path(source) / 'src/range42_exercise_platform/registry_dispatch.py').is_file()
    client, _, _ = env
    from app.core.identity_runtime import identity_request
    monkeypatch.setattr(module(), 'identity_request', identity_request)
    config = tmp_path / 'identity-ssh-config'
    config.write_text('# fixture: transport invokes the real installed dispatcher locally\n')
    config.chmod(0o600)
    monkeypatch.setenv('RANGE42_IDENTITY_SSH_CONFIG', str(config))
    original = asyncio.create_subprocess_exec
    async def launch(*args, **kwargs):
        assert args == ('ssh', '-F', str(config), '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                        '-o', 'StrictHostKeyChecking=yes', 'range42-identities')
        return await original(sys.executable, '-m', 'range42_exercise_platform.registry_dispatch',
            '--store', str(tmp_path / 'protected-registry'), env={**os.environ, 'PYTHONPATH': str(Path(source) / 'src')}, **kwargs)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', launch)
    review = (await client.get('/v1/deployments/run-a/identities')).json()
    async def change(operation):
        nonlocal review
        response = await client.post('/v1/deployments/run-a/identities', json={
            'registry_id': review['state']['registry_id'], 'expected_revision': review['state']['revision'],
            'run_revision': review['run_revision'], 'operation': operation})
        assert response.status_code == 200, response.text
        review = response.json()
    await change({'type': 'scope.register'})
    await change({'type': 'person.register', 'record_ref': 'user01', 'kind': 'temporary'})
    identity = review['state']['people'][0]['id']
    await change({'type': 'membership.set', 'person_id': identity, 'role': 'participant', 'team_ref': 'user01'})
    assert review['state']['memberships'][0]['effective'] is True
    assert review['state']['people'][0]['reconcile_state'] == 'unconfigured'
    assert not review['state']['jobs']
    await change({'type': 'person.register', 'record_ref': 'user02', 'kind': 'temporary'})
    other = next(row['person_id'] for row in review['state']['source_references'] if row['record_ref'] == 'user02')
    rejected = await client.post('/v1/deployments/run-a/identities', json={
        'registry_id': review['state']['registry_id'], 'expected_revision': review['state']['revision'],
        'run_revision': review['run_revision'], 'operation': {'type': 'membership.set', 'person_id': other, 'role': 'participant', 'team_ref': 'user01'}})
    assert rejected.status_code == 409
    assert rejected.json()['code'] == 'IDENTITY_SEAT_OCCUPIED'
    before = review['state']['revision']
    await change({'type': 'membership.remove', 'person_id': identity})
    assert not review['state']['memberships'][0]['effective']
    response = await client.post('/v1/deployments/run-a/identities', json={
        'registry_id': review['state']['registry_id'], 'expected_revision': before,
        'run_revision': review['run_revision'], 'operation': {'type': 'membership.set', 'person_id': identity, 'role': 'participant', 'team_ref': 'user01'}})
    assert response.status_code == 409
    assert response.json()['code'] == 'IDENTITY_REVIEW_CONFLICT'
