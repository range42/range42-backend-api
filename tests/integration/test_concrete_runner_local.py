"""Run a concrete scenario with real Ansible against localhost only."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from app.core.runner_detached import DetachedRunner


@pytest.fixture
def runner_binary() -> str:
    binary = Path(sys.executable).parent / "ansible-runner"
    resolved = str(binary) if binary.is_file() else shutil.which("ansible-runner")
    if not resolved:
        pytest.skip("ansible-runner is not installed")
    return resolved


@pytest.fixture
def local_scenario(tmp_path: Path, runner_binary: str):
    project = tmp_path / "pinned project"
    scenario = project / "scenarios" / "content_smoke"
    (scenario / "files").mkdir(parents=True)
    (scenario / "scripts").mkdir()
    (scenario / "files" / "message.txt").write_text("pinned scenario content\n")
    (scenario / "scripts" / "process.sh").write_text(
        '#!/bin/sh\nset -eu\ntr "[:lower:]" "[:upper:]" < "$1" > "$2"\n'
    )
    (scenario / "hosts.yml").write_text(yaml.safe_dump({
        "all": {"hosts": {"content_target": {
            "ansible_connection": "local",
            "ansible_python_interpreter": sys.executable,
        }}},
    }))
    config = tmp_path / "ansible.cfg"
    config.write_text("[defaults]\nretry_files_enabled = False\n")
    return project, scenario, {
        "PATH": f"{Path(runner_binary).parent}{os.pathsep}{os.environ['PATH']}",
        "ANSIBLE_CONFIG": str(config),
        "CONTENT_ENV": "injected value with spaces",
    }


async def _run(runner_binary, project, scenario, env, private_data_dir, output, extra_vars=None):
    runner = DetachedRunner(runner_bin=runner_binary)
    handle = await runner.start(
        private_data_dir=private_data_dir,
        extravars={
            "r42_project_dir": str(project),
            "r42_playbook_path": str(scenario / "main.yml"),
            "r42_inventory_path": str(scenario / "hosts.yml"),
            "output_dir": str(output),
            **(extra_vars or {}),
        },
        envvars=env,
    )
    try:
        rc = await asyncio.wait_for(handle.wait(), timeout=30)
    finally:
        await handle.kill()
    events = [json.loads(p.read_text()) for p in (private_data_dir / "job_events").glob("*.json")]
    return rc, events


@pytest.mark.asyncio
async def test_concrete_scenario_copies_content_runs_script_and_imports_playbook(
    tmp_path, runner_binary, local_scenario,
):
    project, scenario, env = local_scenario
    output = tmp_path / "guest output"
    output.mkdir()
    (scenario / "main.yml").write_text(yaml.safe_dump([
        {
            "name": "Configure uploaded content",
            "hosts": "content_target",
            "gather_facts": False,
            "tasks": [
                {"name": "Copy content", "ansible.builtin.copy": {
                    "src": "files/message.txt", "dest": "{{ output_dir }}/message.txt", "mode": "0600",
                }},
                {"name": "Run script", "ansible.builtin.script": {
                    "cmd": 'scripts/process.sh "{{ output_dir }}/message.txt" "{{ output_dir }}/processed.txt"',
                }},
            ],
        },
        {"ansible.builtin.import_playbook": "verify.yml"},
    ], sort_keys=False))
    (scenario / "verify.yml").write_text(yaml.safe_dump([{
        "name": "Verify additional orchestration",
        "hosts": "content_target",
        "gather_facts": False,
        "tasks": [{"name": "Use runner environment", "ansible.builtin.copy": {
            "content": "{{ lookup('env', 'CONTENT_ENV') }}", "dest": "{{ output_dir }}/environment.txt",
            "mode": "0600",
        }}],
    }], sort_keys=False))
    art = tmp_path / "attempt"

    rc, events = await _run(runner_binary, project, scenario, env, art, output)

    assert rc == 0, (art / "stdout").read_text() if (art / "stdout").exists() else "no runner output"
    assert (output / "message.txt").read_text() == "pinned scenario content\n"
    assert (output / "processed.txt").read_text() == "PINNED SCENARIO CONTENT\n"
    assert (output / "environment.txt").read_text() == env["CONTENT_ENV"]
    assert (art / "rc").read_text().strip() == "0"
    assert (art / "status").read_text().strip() == "successful"
    assert {e.get("event_data", {}).get("task") for e in events} >= {
        "Copy content", "Run script", "Use runner environment",
    }


@pytest.mark.asyncio
async def test_concrete_scenario_returns_playbook_failure_and_events(
    tmp_path, runner_binary, local_scenario,
):
    project, scenario, env = local_scenario
    (scenario / "main.yml").write_text(yaml.safe_dump([{
        "hosts": "content_target",
        "gather_facts": False,
        "tasks": [{"name": "Fail verification", "ansible.builtin.fail": {"msg": "intentional local failure"}}],
    }]))
    art = tmp_path / "failed-attempt"

    rc, events = await _run(runner_binary, project, scenario, env, art, tmp_path)

    assert rc == 2
    assert (art / "rc").read_text().strip() == "2"
    assert (art / "status").read_text().strip() == "failed"
    assert any(e.get("event") == "runner_on_failed" for e in events)


@pytest.mark.asyncio
async def test_concrete_runner_preserves_literal_credentials_and_templates_ordinary_vars(
    tmp_path, runner_binary, local_scenario,
):
    project, scenario, env = local_scenario
    output = tmp_path / "credential output"
    output.mkdir()
    credentials = {
        "default_admin_vm_ci_password": "  synthetic-{{ expansion_probe }}-päss\n'quoted'\\tail  ",
        "proxmox_api_token_secret": "synthetic-{{ expansion_probe }}-token:{% raw %}literal{% endraw %}",
    }
    variables = {
        **credentials,
        "ordinary_value": "ordinary-{{ expansion_probe }}",
        "expansion_probe": "evaluated",
    }
    (scenario / "main.yml").write_text(yaml.safe_dump([{
        "hosts": "content_target",
        "gather_facts": False,
        "tasks": [{
            "name": f"Write {key}",
            "ansible.builtin.copy": {
                "content": "{{ " + key + " }}",
                "dest": "{{ output_dir }}/" + key,
                "mode": "0600",
            },
            "no_log": True,
        } for key in [*credentials, "ordinary_value"]],
    }], sort_keys=False))
    art = tmp_path / "literal-credentials-attempt"

    rc, _ = await _run(
        runner_binary, project, scenario, env, art, output, extra_vars=variables,
    )

    assert rc == 0, (art / "stdout").read_text()
    for key, expected in credentials.items():
        assert (output / key).read_text() == expected
    assert (output / "ordinary_value").read_text() == "ordinary-evaluated"
    assert (art / "env" / "extravars").stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_checked_in_content_example_runs_with_actual_ansible(
    tmp_path, runner_binary, local_scenario,
):
    project = Path(__file__).resolve().parents[2] / "examples" / "concrete-project"
    scenario = project / "scenarios" / "content_demo"
    assert (scenario / "main.yml").is_file(), "The reviewable concrete-project example is missing"
    _, _, env = local_scenario
    workspace = tmp_path / "example workspace"
    art = tmp_path / "example attempt"

    rc, events = await _run(
        runner_binary, project, scenario, env, art, tmp_path,
        extra_vars={"r42_workspace_dir": str(workspace)},
    )

    assert rc == 0, (art / "stdout").read_text() if (art / "stdout").exists() else "no runner output"
    output = workspace / "demo-output"
    original = (scenario / "files" / "message.txt").read_text()
    assert (output / "message.txt").read_text() == original
    assert (output / "processed.txt").read_text() == original.upper()
    assert json.loads((output / "orchestration.json").read_text()) == {
        "scenario": "content_demo", "content_verified": True,
    }
    assert any(
        e.get("event") == "runner_on_ok"
        and e.get("event_data", {}).get("task") == "Verify the processed content"
        for e in events
    )
