"""Runtime requests execute only the guarded installed composite wrapper."""
import asyncio
import json
from pathlib import Path

import pytest
import yaml

from app.core.models import Attempt, Deployment
from app.core.errors import Range42Error
from tests.core.test_runtime_operations import state
from tests.fixtures.fake_runner import FakeRunner
from tests.routes.test_project_scenario_execution import _boot, seed_scenario


@pytest.mark.asyncio
@pytest.mark.parametrize("ownership_changed", [False, True])
async def test_runtime_uses_bound_inventory_and_rechecks_ownership_before_launch(tmp_path, monkeypatch, ownership_changed):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    from app.core import deploy_trigger
    profile = {"fingerprint": "a" * 64, "dependencies": []}
    finished = []

    class Recorder(FakeRunner):
        async def start(self, **kwargs):
            self.arguments = kwargs
            finished.append(True)
            return await super().start(**kwargs)

    runner = Recorder()
    try:
        ws, sha = await seed_scenario(dbmod, tmp_path, extra_files={
            "manifest/scenario_vms.json": json.dumps({"version": 2, "vms": [
                {"vm_id": 3191, "vm_name": "owned-guest"},
            ]}),
        })
        # A fixture bundle is inert. The assertion below checks that the real
        # runner receives it through the backend-generated guarded wrapper.
        bundle = tmp_path / "bundles/firewall/in_proxmox/firewall.enable.vm/main.yml"
        bundle.parent.mkdir(parents=True)
        bundle.write_text("- hosts: proxmox\n  tasks: []\n")
        monkeypatch.setenv("RANGE42_BUNDLE_DIR", str(tmp_path / "bundles"))
        from app.core import runtime_runner
        monkeypatch.setattr(runtime_runner, "operation_profile", lambda kind: profile)
        calls = 0

        async def observe(*args, **kwargs):
            nonlocal calls
            calls += 1
            current = state()
            current["vms"][0]["firewall_enabled"] = True
            current["vms"][0]["nics"][0]["firewall_enabled"] = True
            if ownership_changed and calls > 1:
                current["vms"][0]["status"] = "conflict"
            return current

        monkeypatch.setattr(runtime_runner, "read_runtime_state", observe)
        from app.core import runtime_completion
        monkeypatch.setattr(runtime_completion, "read_runtime_state", observe)
        async with dbmod.get_session_factory()() as session:
            dep = await session.get(Deployment, "dep-1")
            dep.current_attempt_id = "runtime"
            attempt = Attempt(id="runtime", deployment_id=dep.id, scope="runtime", project_sha=sha,
                              state="pending", operation={"request": {"kind": "vm_firewall", "vm_id": 3191, "enabled": True},
                              "project_sha": sha, "target_host_id": "h", "runtime": profile})
            session.add(attempt)
            await session.commit()
            if ownership_changed:
                with pytest.raises(Range42Error):
                    await deploy_trigger.start_attempt(session, attempt=attempt, runner=runner)
                assert not finished
                return
            await deploy_trigger.start_attempt(session, attempt=attempt, runner=runner)
        variables = runner.arguments["extravars"]
        wrapper = Path(variables["r42_playbook_path"])
        assert wrapper.is_relative_to(ws / "runner/runtime")
        assert not wrapper.is_relative_to(ws / "runner/runtime/checkout")
        plays = yaml.safe_load(wrapper.read_text())
        assert plays[-1]["ansible.builtin.import_playbook"] == str(bundle)
        assert plays[-1]["vars"] == {"BUNDLE_VM_ID": 3191}
        assert "range42-deployment:dep-1" in wrapper.read_text()
        inventory = yaml.safe_load(Path(variables["r42_inventory_path"]).read_text())
        assert inventory["all"]["children"]["proxmox"]["hosts"]["r42-proxmox"]["ansible_connection"] == "local"
        assert "RANGE42_PROVISIONING_LOCK_FD" in runner.arguments["envvars"]
        assert calls >= 2
        await asyncio.gather(*list(deploy_trigger._BACKGROUND_TASKS))
        async with dbmod.get_session_factory()() as session:
            attempt = await session.get(Attempt, "runtime")
            assert attempt.state == "succeeded"
            assert attempt.operation_result["desired_reached"] is True
            assert attempt.operation_result["matched_vmids"] == [3191]
    finally:
        await asyncio.gather(*list(deploy_trigger._BACKGROUND_TASKS), return_exceptions=True)
        await dbmod.dispose_engine()
