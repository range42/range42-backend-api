"""Exercise filesystem boundaries and the real pinned runner contract."""
import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.runner_detached import DetachedRunner, signal_running_attempt
from app.core.workspace import Workspace, WorkspaceError


@pytest.mark.parametrize('label', ['../../../outside', '../x', '/absolute', 'a/../../outside'])
def test_workspace_rejects_traversal(tmp_path, label):
    with pytest.raises(WorkspaceError):
        Workspace.create(codename='AA', scenario_label=label, workspace_root=tmp_path / 'root')


def test_workspace_rejects_symlink_escape(tmp_path):
    root = tmp_path / 'root'
    outside = tmp_path / 'outside'
    root.mkdir()
    outside.mkdir()
    (root / 'AA-demo').symlink_to(outside, target_is_directory=True)
    with pytest.raises(WorkspaceError):
        Workspace.create(codename='AA', scenario_label='demo', workspace_root=root)
    assert not list(outside.iterdir())


@pytest.mark.asyncio
async def test_cancel_uses_current_attempt_pid(tmp_path):
    pidfile = tmp_path / 'runner' / 'attempt' / 'pid'
    pidfile.parent.mkdir(parents=True)
    pidfile.write_text('12345')
    with patch('app.core.runner_detached.os.kill') as kill:
        assert await signal_running_attempt(tmp_path, attempt_id='attempt')
        kill.assert_called_once()


@pytest.mark.asyncio
async def test_real_runner_waits_for_execution_and_exposes_events(tmp_path, monkeypatch):
    monkeypatch.setenv('PATH', str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'])
    playbook = tmp_path / 'playbooks/scenarios/demo/main.yml'
    playbook.parent.mkdir(parents=True)
    marker = tmp_path / 'finished'
    playbook.write_text(
        '- hosts: localhost\n  connection: local\n  gather_facts: false\n  tasks:\n'
        '    - name: wait before completion\n      ansible.builtin.command: /bin/sleep 1\n'
        f'    - ansible.builtin.copy:\n        content: done\n        dest: {marker}\n'
    )
    inventory = tmp_path / 'inventory'
    inventory.mkdir()
    (inventory / 'hosts').write_text('localhost ansible_connection=local\n')
    runner = DetachedRunner(runner_bin=str(Path(sys.executable).with_name('ansible-runner')))
    handle = await runner.start(
        private_data_dir=tmp_path / 'run',
        extravars={'r42_playbook_path': str(playbook), 'r42_inventory_dir': str(inventory)},
        envvars={'ANSIBLE_FORKS': '2'},
    )
    assert await asyncio.wait_for(handle.wait(), 30) == 0
    assert marker.read_text() == 'done'
    events = list(handle.artifact_dir.glob('job_events/*.json'))
    assert any(json.loads(p.read_text()).get('event') == 'runner_on_ok' for p in events)


@pytest.mark.asyncio
async def test_real_runner_cancel_stops_playbook(tmp_path, monkeypatch):
    monkeypatch.setenv('PATH', str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'])
    playbook = tmp_path / 'playbooks/scenarios/demo/main.yml'
    playbook.parent.mkdir(parents=True)
    marker = tmp_path / 'must-not-exist'
    playbook.write_text(
        '- hosts: localhost\n  connection: local\n  gather_facts: false\n  tasks:\n'
        '    - name: blocking task\n      ansible.builtin.command: /bin/sleep 20\n'
        f'    - ansible.builtin.copy:\n        content: unsafe\n        dest: {marker}\n'
    )
    inventory = tmp_path / 'inventory'
    inventory.mkdir()
    (inventory / 'hosts').write_text('localhost ansible_connection=local\n')
    runner = DetachedRunner(runner_bin=str(Path(sys.executable).with_name('ansible-runner')))
    private = tmp_path / 'ws/runner/attempt'
    handle = await runner.start(private_data_dir=private,
                               extravars={'r42_playbook_path': str(playbook),
                                          'r42_inventory_dir': str(inventory)}, envvars={})
    (private / 'pid').write_text(str(handle.pid))
    try:
        async with asyncio.timeout(15):
            while not list(handle.artifact_dir.glob('job_events/*')):
                await asyncio.sleep(.05)
        assert await signal_running_attempt(tmp_path / 'ws', attempt_id='attempt')
        assert await asyncio.wait_for(handle.wait(), 10) != 0
        assert not marker.exists()
    finally:
        await handle.kill()
