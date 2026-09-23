"""Native consumers must see the attempt's pinned scenario through the API runner."""
import asyncio
import json
import os
from pathlib import Path
import sys

import pytest
import yaml

from app.core.models import Attempt
from app.core.runner_detached import DetachedRunner
from tests.routes.test_project_scenario_execution import _boot, seed_scenario


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["full", "configure", "teardown"])
async def test_native_report_reads_pinned_manifest_and_workspace_vault(tmp_path, monkeypatch, scope):
    fixture = os.getenv("RANGE42_NATIVE_CONTRACT_FIXTURE")
    if not fixture:
        if os.getenv("CI"):
            pytest.fail("Native contract fixture is required in CI")
        pytest.skip("Set RANGE42_NATIVE_CONTRACT_FIXTURE to the reviewed native checkout")
    report = Path(fixture) / "playbooks/bundles/firewall/in_proxmox/firewall.report.status/main.yml"
    assert report.is_file()
    cfg = tmp_path / "ansible.cfg"
    cfg.write_text("[defaults]\nretry_files_enabled=False\n")
    monkeypatch.setenv("ANSIBLE_CONFIG", str(cfg))
    monkeypatch.setenv("PATH", f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}")
    role = tmp_path / "roles/range42-ansible_roles-proxmox_controller/tasks"
    role.mkdir(parents=True)
    # Only replace external PVE calls. Execute the installed report unchanged.
    (role / "main.yml").write_text(yaml.safe_dump([
        {"ansible.builtin.set_fact": {"vm_list": [
            {"vm_id": 5000, "vm_template": 0}, {"vm_id": 9000, "vm_template": 0},
        ]}, "when": "proxmox_vm_action == 'vm_list'"},
    ]))
    monkeypatch.setenv("ANSIBLE_ROLES_PATH", str(tmp_path / "roles"))
    _, dbmod = await _boot(tmp_path, monkeypatch)
    from app.core import deploy_trigger
    plays = yaml.safe_dump([
        {"ansible.builtin.import_playbook": str(report)},
        {"hosts": "proxmox", "gather_facts": False, "tasks": [{"ansible.builtin.assert": {"that": [
            "_fwrst_manifest.vms | map(attribute='vm_id') | list == [5000, 5001]",
            "_fwrst_ids == [5000]", "context_vault_marker == 'original workspace'",
        ]}}], "vars_files": ["{{ lookup('env', 'RANGE42_ACTIVE_CONFIG_DIR') }}/secrets/default_vault.yml"]},
    ])
    try:
        ws, _ = await seed_scenario(dbmod, tmp_path, vmids=(5000, 5001), extra_files={
            **{name: plays for name in ("main.yml", "configure.yml", "teardown.yml")},
            "hosts.yml": "proxmox:\n  hosts:\n    localhost:\n      ansible_connection: local\n",
        })
        (ws / "secrets").mkdir(exist_ok=True)
        vault = ws / "secrets/default_vault.yml"
        vault.write_text("context_vault_marker: original workspace\n")
        # Stale workspace metadata must never select another deployment's VMs.
        stale = ws / "scenario/manifest/scenario_vms.json"
        stale.parent.mkdir(parents=True)
        stale.write_text(json.dumps({"vms": [{"vm_id": 9000}]}))
        async with dbmod.get_session_factory()() as session:
            attempt = Attempt(id="native-context", deployment_id="dep-1", scope=scope, state="pending")
            session.add(attempt)
            await session.commit()
            await deploy_trigger.start_attempt(session, attempt=attempt, runner=DetachedRunner(
                runner_bin=str(Path(sys.executable).parent / "ansible-runner")))
        await asyncio.wait_for(asyncio.gather(*list(deploy_trigger._BACKGROUND_TASKS)), timeout=45)
        async with dbmod.get_session_factory()() as session:
            result = await session.get(Attempt, "native-context")
            assert result.state == "succeeded", (ws / "events.jsonl").read_text()
        assert vault.read_text() == "context_vault_marker: original workspace\n"
        assert json.loads(stale.read_text()) == {"vms": [{"vm_id": 9000}]}
    finally:
        await asyncio.gather(*list(deploy_trigger._BACKGROUND_TASKS), return_exceptions=True)
        await dbmod.dispose_engine()
