"""Runtime requests execute only the guarded installed composite wrapper."""
import asyncio
import json
from pathlib import Path

import pytest
import yaml

from app.core.models import Attempt, Deployment, ProxmoxHost
from app.core.errors import Range42Error
from tests.core.test_runtime_operations import state
from app.core.runner_detached import DetachedRunner
from tests.routes.test_project_scenario_execution import _boot, seed_scenario


@pytest.mark.asyncio
@pytest.mark.parametrize("ownership_changed", [False, True])
@pytest.mark.parametrize("native", [False, True])
async def test_runtime_uses_bound_inventory_and_rechecks_ownership_before_launch(tmp_path, monkeypatch, ownership_changed, native):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    from app.core import deploy_trigger
    profile = {"fingerprint": "a" * 64, "dependencies": []}
    if native:
        profile["contract"] = "native-sdn-20260921"
    finished = []

    executable = tmp_path / 'inert-runner'
    executable.write_text('#!/bin/sh\nprintf 0 > "$2/rc"\nprintf successful > "$2/status"\n')
    executable.chmod(0o700)

    class Recorder(DetachedRunner):
        async def start(self, **kwargs):
            self.arguments = kwargs
            finished.append(True)
            return await super().start(**kwargs)

    # Keep the real runner's path-containment checks; only replace the external
    # executable so this integration test cannot mutate Proxmox.
    runner = Recorder(runner_bin=str(executable))
    try:
        ws, sha = await seed_scenario(dbmod, tmp_path, extra_files={
            "manifest/scenario_vms.json": json.dumps({"version": 2, "vms": [
                {"vm_id": 3191, "vm_name": "owned-guest"},
            ]}),
        })
        (ws / "secrets").mkdir(exist_ok=True)
        (ws / "secrets/default_vault.yml").write_text("vm_fw_mgmt_source: 10.42.0.0/24\n")
        # A fixture bundle is inert. The assertion below checks that the real
        # runner receives it through the backend-generated guarded wrapper.
        bundle = tmp_path / "bundles/firewall/in_proxmox/firewall.enable.vm/main.yml"
        bundle.parent.mkdir(parents=True)
        bundle.write_text("- hosts: proxmox\n  tasks: []\n")
        monkeypatch.setenv("RANGE42_BUNDLE_DIR", str(tmp_path / "bundles"))
        from app.core import runtime_runner
        monkeypatch.setattr(runtime_runner, "operation_profile", lambda kind: profile)
        calls = 0

        async def observe(scenario_dir, *args, **kwargs):
            nonlocal calls
            calls += 1
            assert Path(scenario_dir) == ws / "runner/runtime/checkout/labs/demo/scenarios/content"
            assert json.loads((Path(scenario_dir) / "manifest/scenario_vms.json").read_text())["vms"] == [
                {"vm_id": 3191, "vm_name": "owned-guest"},
            ]
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
            host = await session.get(ProxmoxHost, "h")
            attempt = Attempt(id="runtime", deployment_id=dep.id, scope="runtime", project_sha=sha,
                              state="pending", operation={"request": {"kind": "vm_firewall", "vm_id": 3191, "enabled": True},
                              "project_sha": sha, "target_host_id": "h", "runtime": profile,
                              "target_identity": {"api_url": host.api_url.rstrip("/"), "node_name": host.node_name}})
            session.add(attempt)
            await session.commit()
            if ownership_changed:
                with pytest.raises(Range42Error):
                    await deploy_trigger.start_attempt(session, attempt=attempt, runner=runner)
                assert not finished
                return
            await deploy_trigger.start_attempt(session, attempt=attempt, runner=runner)
        variables = runner.arguments["extravars"]
        assert variables["BUNDLE_VM_ID"] == 3191
        vault = Path(runner.arguments["envvars"]["RANGE42_ACTIVE_CONFIG_DIR"]) / "secrets/default_vault.yml"
        assert yaml.safe_load(vault.read_text())["vm_fw_mgmt_source"] == "10.42.0.0/24"
        wrapper = Path(variables["r42_playbook_path"])
        assert wrapper.is_relative_to(ws / "runner/runtime")
        assert not wrapper.is_relative_to(ws / "runner/runtime/checkout")
        assert Path(variables["r42_project_dir"]) == wrapper.parent
        assert (ws / "runner/runtime/project").resolve() == wrapper.parent
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


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["api_url", "node_name", "missing_binding"])
async def test_target_reregistration_cannot_redirect_a_queued_operation(tmp_path, monkeypatch, change):
    from types import SimpleNamespace
    from app.core import runtime_runner
    from app.core.models import ProxmoxHost
    _, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        _, sha = await seed_scenario(dbmod, tmp_path)
        profile = {"fingerprint": "a" * 64, "dependencies": []}
        monkeypatch.setattr(runtime_runner, "operation_profile", lambda kind: profile)
        async def observe(*args, **kwargs):
            return state()
        monkeypatch.setattr(runtime_runner, "read_runtime_state", observe)
        async with dbmod.get_session_factory()() as session:
            dep = await session.get(Deployment, "dep-1")
            host = await session.get(ProxmoxHost, "h")
        operation = {"request": {"kind": "vm_firewall", "vm_id": 3191, "enabled": True},
            "project_sha": sha, "target_host_id": "h", "runtime": profile,
            "target_identity": {"api_url": host.api_url.rstrip("/"), "node_name": host.node_name}}
        if change == "missing_binding":
            operation.pop("target_identity")
        else:
            async with dbmod.get_session_factory()() as session:
                fresh = await session.get(ProxmoxHost, "h")
                setattr(fresh, change, "https://other-node:8006" if change == "api_url" else "other-node")
                await session.commit()
        # Keep the original detached host object: the final preparation check
        # must consult the durable registration rather than stale ORM values.
        with pytest.raises(Range42Error) as error:
            await runtime_runner._verified_plan(dep, SimpleNamespace(operation=operation, project_sha=sha), host, tmp_path)
        assert error.value.code == "RUNTIME_TARGET_CHANGED"
    finally:
        await dbmod.dispose_engine()
