import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.core import deploy_trigger
from app.core.db import build_engine, session_factory
from app.core.errors import Range42Error
from app.core.models import Attempt, Base, Deployment, Project, ProxmoxHost, Source, WorkspaceLock


@pytest.fixture
async def lifecycle(tmp_path, monkeypatch):
    engine = build_engine(f'sqlite+aiosqlite:///{tmp_path}/state.db')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = session_factory(engine)
    playbook = tmp_path / 'playbooks/scenarios/demo/main.yml'
    playbook.parent.mkdir(parents=True)
    playbook.write_text('- hosts: localhost\n  tasks: []\n')
    monkeypatch.setenv('API_BACKEND_WWWAPP_PLAYBOOKS_DIR', str(tmp_path / 'playbooks'))
    ws = tmp_path / 'workspace'
    (ws / 'inventory').mkdir(parents=True)
    async with sf() as s:
        s.add(Source(id='s', provider='github', base_url='url', auth_kind='none'))
        s.add(ProxmoxHost(id='h', name='h', api_url='url', node_name='h', token_ref='t'))
        await s.commit()
        s.add(Project(id='p', name='p', source_id='s', branch_strategy='shared_repo_subdir'))
        await s.commit()
        s.add(Deployment(id='d', codename='AA', scenario_label='demo', project_id='p',
                         target_host_id='h', workspace_path=str(ws), state='pending'))
        await s.commit()
        s.add(Attempt(id='a', deployment_id='d', scope='full', state='pending'))
        await s.commit()
    yield sf, ws, playbook
    for task in list(deploy_trigger._BACKGROUND_TASKS):
        task.cancel()
    await asyncio.gather(*list(deploy_trigger._BACKGROUND_TASKS), return_exceptions=True)
    await engine.dispose()


class ControlledRunner:
    def __init__(self, rc=0):
        self.done = asyncio.Event()
        self.rc = rc
        self.pid = 12345

    async def start(self, *, private_data_dir, **kwargs):
        self.artifact_dir = private_data_dir / 'artifacts' / 'execution'
        (self.artifact_dir / 'job_events').mkdir(parents=True)
        return self

    async def wait(self):
        await self.done.wait()
        (self.artifact_dir / 'job_events' / '1.json').write_text(json.dumps({
            'event': 'runner_on_ok', 'event_data': {'task': 'last task', 'host': 'localhost'}}))
        return self.rc


@pytest.mark.parametrize('rc, expected', [(0, 'succeeded'), (2, 'failed')])
async def test_execution_persists_state_drains_events_and_releases_lock(lifecycle, rc, expected):
    sf, ws, _ = lifecycle
    runner = ControlledRunner(rc)
    async with sf() as s:
        await deploy_trigger.start_attempt(s, attempt=await s.get(Attempt, 'a'), runner=runner)
    async with sf() as s:
        assert (await s.get(Attempt, 'a')).state == 'deploying'
        assert (await s.get(Deployment, 'd')).current_attempt_id == 'a'
    runner.done.set()
    await asyncio.gather(*list(deploy_trigger._BACKGROUND_TASKS))
    async with sf() as s:
        att = await s.get(Attempt, 'a')
        assert att.state == expected
        assert att.rc == rc and att.ended_at is not None
        assert (await s.get(Deployment, 'd')).state == expected
        assert await s.get(WorkspaceLock, 'd') is None
    assert 'last task' in (ws / 'events.jsonl').read_text()


async def test_setup_failure_marks_failed_and_releases_lock(lifecycle):
    sf, _, playbook = lifecycle
    playbook.unlink()
    async with sf() as s:
        with pytest.raises(Exception):
            await deploy_trigger.start_attempt(s, attempt=await s.get(Attempt, 'a'))
    async with sf() as s:
        assert (await s.get(Attempt, 'a')).state == 'failed'
        assert await s.get(WorkspaceLock, 'd') is None


async def test_long_run_renews_lock(lifecycle, monkeypatch):
    sf, _, _ = lifecycle
    monkeypatch.setattr(deploy_trigger, 'HEARTBEAT_INTERVAL_S', .02, raising=False)
    runner = ControlledRunner()
    async with sf() as s:
        await deploy_trigger.start_attempt(s, attempt=await s.get(Attempt, 'a'), runner=runner)
    async with sf() as s:
        lock = await s.get(WorkspaceLock, 'd')
        lock.heartbeat_at = datetime.now(timezone.utc) - timedelta(seconds=120)
        await s.commit()
    await asyncio.sleep(.1)
    async with sf() as s:
        lock = await s.get(WorkspaceLock, 'd')
        age = datetime.now(timezone.utc) - lock.heartbeat_at.replace(tzinfo=timezone.utc)
        assert age.total_seconds() < 1
    runner.done.set()
    await asyncio.gather(*list(deploy_trigger._BACKGROUND_TASKS))


async def test_teardown_without_operation_playbook_preserves_workspace(lifecycle):
    from app.routes.v1.deployments.teardown import teardown
    from app.schemas.v1.deployments import TeardownRequest
    sf, ws, _ = lifecycle
    (ws / 'inventory/hosts').write_text('precious inventory')
    async with sf() as s:
        with pytest.raises(Range42Error) as error:
            await teardown('d', TeardownRequest(confirm_codename='AA'), s)
        assert error.value.code == 'OPERATION_UNSUPPORTED'
    assert (ws / 'inventory/hosts').read_text() == 'precious inventory'


async def test_supported_teardown_starts_runner_and_preserves_workspace(lifecycle, monkeypatch):
    from app.routes.v1.deployments.teardown import teardown
    from app.schemas.v1.deployments import TeardownRequest
    sf, ws, playbook = lifecycle
    playbook.with_name('teardown.yml').write_text('- hosts: localhost\n  tasks: []\n')
    runner = ControlledRunner()
    monkeypatch.setattr(deploy_trigger, 'DetachedRunner', lambda: runner)
    async with sf() as s:
        response = await teardown('d', TeardownRequest(confirm_codename='AA'), s)
        assert response.state == 'deploying'
        assert (await s.get(Deployment, 'd')).current_attempt_id == response.id
    assert ws.exists()
    runner.done.set()
    await asyncio.gather(*list(deploy_trigger._BACKGROUND_TASKS))


async def test_cancel_does_not_claim_success_when_signal_fails(lifecycle, monkeypatch):
    from app.routes.v1.deployments.snapshots import cancel_current_attempt
    sf, _, _ = lifecycle
    async with sf() as s:
        dep = await s.get(Deployment, 'd')
        dep.current_attempt_id = 'a'
        await s.commit()
        monkeypatch.setattr('app.core.runner_detached.signal_running_attempt', AsyncMock(return_value=False))
        with pytest.raises(Range42Error):
            await cancel_current_attempt('d', s)
        assert (await s.get(Attempt, 'a')).state == 'pending'


async def test_busy_workspace_does_not_replace_current_attempt(lifecycle):
    from app.routes.v1.deployments.attempts import create_attempt
    from app.schemas.v1.deployments import AttemptCreate
    sf, _, _ = lifecycle
    runner = ControlledRunner()
    async with sf() as s:
        await deploy_trigger.start_attempt(s, attempt=await s.get(Attempt, 'a'), runner=runner)
    async with sf() as s:
        with pytest.raises(Range42Error) as error:
            await create_attempt('d', AttemptCreate(scope='full'), s)
        assert error.value.status == 409
    async with sf() as s:
        assert (await s.get(Deployment, 'd')).current_attempt_id == 'a'
        assert len((await s.scalars(select(Attempt))).all()) == 1
    runner.done.set()
    await asyncio.gather(*list(deploy_trigger._BACKGROUND_TASKS))


async def test_concurrent_submissions_only_start_one_runner(lifecycle, monkeypatch):
    from app.routes.v1.deployments.attempts import create_attempt
    from app.schemas.v1.deployments import AttemptCreate
    sf, _, _ = lifecycle
    runner = ControlledRunner()
    monkeypatch.setattr(deploy_trigger, 'DetachedRunner', lambda: runner)

    async def submit():
        async with sf() as s:
            try:
                return await create_attempt('d', AttemptCreate(scope='full'), s)
            except Range42Error as error:
                return error.status

    outcomes = await asyncio.gather(submit(), submit(), return_exceptions=True)
    assert sum(getattr(item, 'state', None) == 'deploying' for item in outcomes) == 1
    assert 409 in outcomes
    runner.done.set()
    await asyncio.gather(*list(deploy_trigger._BACKGROUND_TASKS))


@pytest.mark.parametrize('scope', ['teardown', 'rollback_all', 'rollback_team'])
async def test_generic_attempt_cannot_bypass_operation_guards(lifecycle, scope):
    from app.routes.v1.deployments.attempts import create_attempt
    from app.schemas.v1.deployments import AttemptCreate
    sf, _, playbook = lifecycle
    playbook.with_name(scope + '.yml').write_text('- hosts: localhost\n  tasks: []\n')
    async with sf() as s:
        with pytest.raises(Range42Error) as error:
            await create_attempt('d', AttemptCreate(scope=scope, team_id=1), s)
        assert error.value.code == 'USE_SCOPED_ENDPOINT'


async def test_cancel_completion_race_preserves_terminal_state(lifecycle, monkeypatch):
    from app.routes.v1.deployments.snapshots import cancel_current_attempt
    sf, _, _ = lifecycle
    runner = ControlledRunner()
    async with sf() as s:
        await deploy_trigger.start_attempt(s, attempt=await s.get(Attempt, 'a'), runner=runner)

    async def signal(*args, **kwargs):
        runner.done.set()
        await asyncio.sleep(.05)
        return True

    monkeypatch.setattr('app.core.runner_detached.signal_running_attempt', signal)
    async with sf() as s:
        await cancel_current_attempt('d', s)
    await asyncio.gather(*list(deploy_trigger._BACKGROUND_TASKS))
    async with sf() as s:
        att = await s.get(Attempt, 'a')
        assert att.state == 'cancelled'
        assert att.ended_at is not None
        assert (await s.get(Deployment, 'd')).state == 'cancelled'


async def test_event_observer_failure_still_persists_runner_result(lifecycle, monkeypatch):
    sf, _, _ = lifecycle
    runner = ControlledRunner()
    monkeypatch.setattr(deploy_trigger.EventsWatcher, 'run', AsyncMock(side_effect=OSError('unreadable events')))
    async with sf() as s:
        await deploy_trigger.start_attempt(s, attempt=await s.get(Attempt, 'a'), runner=runner)
    runner.done.set()
    await asyncio.gather(*list(deploy_trigger._BACKGROUND_TASKS), return_exceptions=True)
    async with sf() as s:
        att = await s.get(Attempt, 'a')
        assert att.state == 'succeeded' and att.rc == 0
        assert att.sub_reason == 'EVENT_STREAM_FAILED'
        assert await s.get(WorkspaceLock, 'd') is None


async def test_rollback_shared_only_passes_shared_snapshots(lifecycle, monkeypatch):
    from app.core.models import Snapshot
    from app.routes.v1.deployments.snapshots import rollback
    from app.schemas.v1.deployments import RollbackRequest
    sf, _, playbook = lifecycle
    playbook.with_name('rollback_shared.yml').write_text('- hosts: localhost\n  tasks: []\n')
    runner = ControlledRunner()
    captured = {}
    original_start = runner.start

    async def capture(**kwargs):
        captured.update(kwargs['extravars'])
        return await original_start(**kwargs)

    runner.start = capture
    monkeypatch.setattr(deploy_trigger, 'DetachedRunner', lambda: runner)
    async with sf() as s:
        s.add(Snapshot(id='shared', deployment_id='d', vm_id=201, name='shared'))
        s.add(Snapshot(id='team', deployment_id='d', vm_id=202, team_id=1, name='team'))
        await s.commit()
        await rollback('d', RollbackRequest(scope='shared'), s)
    runner.done.set()
    await asyncio.gather(*list(deploy_trigger._BACKGROUND_TASKS))
    assert captured['r42_snapshots'] == [{'vm_id': 201, 'name': 'shared'}]


async def test_rollback_requires_team_for_team_scope(lifecycle):
    from app.routes.v1.deployments.snapshots import rollback
    from app.schemas.v1.deployments import RollbackRequest
    sf, _, _ = lifecycle
    async with sf() as s:
        with pytest.raises(Range42Error) as error:
            await rollback('d', RollbackRequest(scope='team'), s)
        assert error.value.code == 'TEAM_REQUIRED'


async def test_failure_after_spawn_stops_runner_before_releasing_workspace(lifecycle, monkeypatch):
    from pathlib import Path
    sf, _, _ = lifecycle
    runner = ControlledRunner()
    runner.kill = AsyncMock()
    write = Path.write_text

    def fail_pid(path, *args, **kwargs):
        if path.name == 'pid':
            raise OSError('pid file cannot be written')
        return write(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'write_text', fail_pid)
    async with sf() as s:
        with pytest.raises(OSError):
            await deploy_trigger.start_attempt(s, attempt=await s.get(Attempt, 'a'), runner=runner)
    runner.kill.assert_awaited_once()
    async with sf() as s:
        assert await s.get(WorkspaceLock, 'd') is None
        assert (await s.get(Attempt, 'a')).state == 'failed'


async def test_unwritable_event_log_does_not_lose_terminal_state(lifecycle):
    sf, ws, _ = lifecycle
    runner = ControlledRunner()
    async with sf() as s:
        await deploy_trigger.start_attempt(s, attempt=await s.get(Attempt, 'a'), runner=runner)
    (ws / 'events.jsonl').unlink()
    (ws / 'events.jsonl').mkdir()
    runner.done.set()
    await asyncio.gather(*list(deploy_trigger._BACKGROUND_TASKS), return_exceptions=True)
    async with sf() as s:
        assert (await s.get(Attempt, 'a')).state == 'succeeded'
        assert await s.get(WorkspaceLock, 'd') is None
