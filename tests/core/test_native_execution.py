import json
import asyncio
import os
import signal
import subprocess

import pytest

from tests.core.test_native_contexts import context
from tests.core.test_native_scenarios import scenario


def test_native_execution_binds_context_and_pinned_dependencies_without_editing_sources(tmp_path, monkeypatch):
    from app.core.native_contexts import resolve_context
    from app.core.native_execution import prepare_native_run
    from app.core.native_scenarios import inspect_native_scenario
    ws, script, host = context(tmp_path, monkeypatch)
    script.write_text('''range42-context() {
      if [[ "$1" == use ]]; then
        export RANGE42_ACTIVE_CONFIG_DIR="$RANGE42_CONFIG_BASE_DIR/$2-$3"
        source "$RANGE42_ACTIVE_CONFIG_DIR/sourced_range42.sh"
      else
        local target=$(readlink -f "$RANGE42_ACTIVE_CONFIG_DIR/scenario")
        (cd "$target" && bash "$target/${target##*/}.setup.sh")
      fi
    }
    ''')
    repo = tmp_path / "repo"
    base = scenario(repo)
    (repo / "bundles").mkdir()
    (base / "templates/ansible-inventory.j2").write_text('all:\n  hosts:\n    {{ INFRASTRUCTURE_CODENAME }}:\n      ansible_host: {{ INFRASTRUCTURE_PROXMOX_ADDRESS }}\n')
    result = tmp_path / "result.json"
    wrapper = base / "exercise.setup.sh"
    wrapper.write_text('''#!/bin/bash
python3 - <<'PY'
import json,os
from pathlib import Path
Path(os.environ['TEST_NATIVE_RESULT']).write_text(json.dumps({
 'context':os.environ['RANGE42_ACTIVE_CONFIG_DIR'],
 'bundles':os.environ['RANGE42_BUNDLE_DIR'],
 'inventory':Path(os.environ['RANGE42_ANSIBLE_ROLES__INVENTORY_DIR'],'inventory_default.yml').read_text(),
}))
PY
''')
    before = wrapper.read_bytes(), (ws / "scenario").readlink(), (ws / "inventory/inventory_default.yml").read_bytes()
    descriptor = inspect_native_scenario(repo, "training/exercise-a")
    run = prepare_native_run(repo, descriptor, resolve_context("lab-demo", host),
        scope="full", features={"WAZUH": True}, parameters={}, artifact_dir=tmp_path / "run")
    completed = subprocess.run(run.command, env={**os.environ, "TEST_NATIVE_RESULT": str(result)}, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr + completed.stdout
    proof = json.loads(result.read_text())
    assert proof["bundles"] == str(tmp_path / "run/native-repository/bundles")
    assert "lab:" in proof["inventory"] and "192.0.2.10" in proof["inventory"]
    assert proof["context"] != str(ws)
    assert (wrapper.read_bytes(), (ws / "scenario").readlink(), (ws / "inventory/inventory_default.yml").read_bytes()) == before
    assert json.loads(run.variables_file.read_text()) == {"INSTALL_WAZUH": "YES"}


@pytest.mark.asyncio
async def test_detached_native_command_uses_existing_runner_event_and_exit_lifecycle(tmp_path):
    from app.core.runner_detached import DetachedRunner
    art = tmp_path / "attempt"
    result = tmp_path / "ran"
    command = ["/bin/sh", "-c", 'printf native > "$1"; echo native-run-completed', "native-test", str(result)]
    handle = await DetachedRunner().start(private_data_dir=art,
        extravars={"r42_native_command": command}, envvars={})
    assert await handle.wait() == 0
    assert result.read_text() == "native"
    assert (art / "rc").read_text().strip() == "0"
    assert list((art / "job_events").glob("*.json"))


@pytest.mark.parametrize("subdir", ["", "projects/team"])
def test_native_execution_preserves_relative_shared_dependencies_and_context_secrets(tmp_path, monkeypatch, subdir):
    from app.core.native_contexts import resolve_context
    from app.core.native_execution import prepare_native_run
    from app.core.native_scenarios import inspect_native_scenario
    ws, script, host = context(tmp_path, monkeypatch)
    script.write_text('''range42-context() {
      if [[ "$1" == use ]]; then
        export RANGE42_ACTIVE_CONFIG_DIR="$RANGE42_CONFIG_BASE_DIR/$2-$3"
      else
        local target=$(readlink -f "$RANGE42_ACTIVE_CONFIG_DIR/scenario")
        (cd "$target" && bash "$target/${target##*/}.setup.sh")
      fi
    }
    ''')
    repo = tmp_path / "repo"
    project_root = repo / subdir
    base = scenario(project_root)
    (repo / "bundles").mkdir()
    (repo / "bundles/shared.yml").write_text('''- ansible.builtin.assert:
    that:
      - INSTALL_WAZUH == 'YES'
      - lookup('file', 'secrets/fixture') == 'context-owned'
''')
    (ws / "secrets/fixture").write_text("context-owned")
    (base / "main.yml").write_text('''- hosts: localhost
  gather_facts: false
  tasks:
    - ansible.builtin.include_tasks: ''' + os.path.relpath(repo / 'bundles/shared.yml', base) + '\n')
    # Older wrappers ignore "$@"; feature selections must still reach Ansible.
    (base / "exercise.setup.sh").write_text("ansible-playbook -i localhost, -c local ./main.yml\n")
    run = prepare_native_run(project_root, inspect_native_scenario(project_root, "training/exercise-a"),
        resolve_context("lab-demo", host), scope="full", features={"WAZUH": True},
        parameters={}, artifact_dir=tmp_path / "run", repository_root=repo)
    completed = subprocess.run(run.command, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert not (base / "secrets").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("recovered", [False, True])
@pytest.mark.parametrize("nested", [False, True])
async def test_native_cancellation_stops_the_command_and_its_child(tmp_path, recovered, nested):
    from app.core.runner_detached import DetachedRunner, _process_identity, signal_process_identity, signal_running_attempt
    artifact = tmp_path / "runner/attempt"
    proof = tmp_path / "children"
    handle = await DetachedRunner().start(private_data_dir=artifact,
        extravars={"r42_native_command": ["/bin/sh", "-c",
            ('sleep 60 & echo "$$ $!" > "$1"; wait' if nested else 'echo "$$" > "$1"; exec sleep 60'),
            "cancel-test", str(proof)]}, envvars={})
    identities = []
    try:
        for _ in range(100):
            if proof.is_file():
                break
            await asyncio.sleep(.05)
        assert proof.is_file(), "native command did not start"
        identities = [_process_identity(int(pid)) for pid in proof.read_text().split()]
        assert all(identities)
        if recovered:
            assert await signal_running_attempt(tmp_path)
        else:
            await handle.kill()
        await asyncio.wait_for(handle.wait(), 10)
        for _ in range(40):
            if all(_process_identity(identity["pid"]) != identity for identity in identities):
                break
            await asyncio.sleep(.05)
        assert all(_process_identity(identity["pid"]) != identity for identity in identities), "deployment command survived Cancel"
        assert (artifact / "status").read_text() == "canceled"
    finally:
        await handle.kill()
        for identity in identities:
            if identity:
                signal_process_identity(identity, signal.SIGKILL)
