"""Concrete operations execute explicit pinned entrypoints and retain history."""
import asyncio
import os
from pathlib import Path
import sys

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from app.core.project import resolve_project_scenario
from app.core.errors import Range42Error
from tests.core.test_project_scenario import scenario_tree
from tests.routes.test_project_scenario_execution import _boot, seed_scenario
from tests.fixtures.fake_runner import FakeRunner


@pytest.mark.parametrize("scope", ["configure", "teardown"])
def test_resolver_uses_only_explicit_operation_playbook(tmp_path, scope):
    scenario = scenario_tree(tmp_path)
    (scenario / f"{scope}.yml").write_text("- hosts: guest\n  tasks: []\n")
    resolved = resolve_project_scenario(tmp_path, scenario_label="content", scope=scope)
    assert resolved.playbook == scenario / f"{scope}.yml"
    (scenario / f"{scope}.yml").unlink()
    with pytest.raises(Range42Error, match=f"{scope}.yml"):
        resolve_project_scenario(tmp_path, scenario_label="content", scope=scope)


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["configure", "teardown"])
async def test_operation_runs_its_real_playbook_and_keeps_workspace(tmp_path, monkeypatch, scope):
    monkeypatch.setenv("RANGE42_AUTO_START_ATTEMPTS", "1")
    monkeypatch.setenv("PATH", f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}")
    cfg = tmp_path / "ansible.cfg"
    cfg.write_text("[defaults]\nretry_files_enabled=False\n")
    monkeypatch.setenv("ANSIBLE_CONFIG", str(cfg))
    app, dbmod = await _boot(tmp_path, monkeypatch)
    from app.core.deploy_trigger import _BACKGROUND_TASKS
    marker_task = {"ansible.builtin.copy": {
        "content": scope, "dest": "{{ r42_workspace_dir }}/operation.txt", "mode": "0600"}}
    try:
        ws, _ = await seed_scenario(dbmod, tmp_path, vmids=(), extra_files={
            "main.yml": "- hosts: guest\n  tasks:\n    - ansible.builtin.fail:\n        msg: main must not run\n",
            f"{scope}.yml": yaml.safe_dump([{"hosts": "guest", "gather_facts": False, "tasks": [marker_task]}]),
        })
        history = ws / "keep-history.txt"
        history.write_text("previous attempt")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            dep = (await client.get("/v1/deployments/dep-1")).json()
            if scope == "teardown":
                response = await client.request("DELETE", "/v1/deployments/dep-1",
                                                json={"confirm_codename": dep["codename"]})
                assert response.status_code == 202, response.text
            else:
                response = await client.post("/v1/deployments/dep-1/attempts", json={"scope": scope})
                assert response.status_code == 201, response.text
            await asyncio.wait_for(asyncio.gather(*list(_BACKGROUND_TASKS)), timeout=30)
            attempts = (await client.get("/v1/deployments/dep-1/attempts")).json()["items"]
        assert attempts[0]["state"] == "succeeded", attempts
        assert attempts[0]["scope"] == scope
        assert attempts[0]["rc"] == 0
        assert history.read_text() == "previous attempt"
        assert (ws / "operation.txt").read_text() == scope
    finally:
        await asyncio.gather(*list(_BACKGROUND_TASKS), return_exceptions=True)
        await dbmod.dispose_engine()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{"scope": "teardown"}, {"scope": "teardown", "confirm_codename": "wrong"}])
async def test_attempt_teardown_requires_confirmation_before_reservation(tmp_path, monkeypatch, payload):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        await seed_scenario(dbmod, tmp_path)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            response = await client.post("/v1/deployments/dep-1/attempts", json=payload)
            assert response.status_code == 400, response.text
            assert response.json()["code"] == "TEARDOWN_CONFIRM_MISMATCH"
            assert (await client.get("/v1/deployments/dep-1/attempts")).json()["total"] == 0
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint,payload", [
    ("teams/1/reset", {}), ("snapshot", {"scope": "all"}), ("rollback", {"scope": "all"}),
])
async def test_unimplemented_operations_reject_without_creating_fake_attempt(tmp_path, monkeypatch, endpoint, payload):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        await seed_scenario(dbmod, tmp_path)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            response = await client.post(f"/v1/deployments/dep-1/{endpoint}", json=payload)
            assert response.status_code == 400, response.text
            assert response.json()["code"] == "PROJECT_SCENARIO_SCOPE_UNSUPPORTED"
            assert (await client.get("/v1/deployments/dep-1/attempts")).json()["total"] == 0
            assert (await client.get("/v1/deployments/dep-1")).json()["current_attempt_id"] is None
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
@pytest.mark.parametrize("start_fails", [False, True])
async def test_runtime_target_injection_and_temporary_vault_cleanup(tmp_path, monkeypatch, start_fails):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    from app.core import deploy_trigger
    seen = {}

    class RecordingRunner(FakeRunner):
        async def start(self, **kwargs):
            seen.update(kwargs)
            vault = Path(kwargs["envvars"]["RANGE42_ACTIVE_CONFIG_DIR"]) / "secrets/default_vault.yml"
            assert vault.read_text() == "{}\n"
            if start_fails:
                raise RuntimeError("intentional runner startup failure")
            return await super().start(**kwargs)

    monkeypatch.setattr(deploy_trigger, "DetachedRunner", lambda: RecordingRunner(script=[]))
    monkeypatch.setenv("RANGE42_AUTO_START_ATTEMPTS", "1")
    try:
        ws, _ = await seed_scenario(dbmod, tmp_path, vmids=())
        finish = deploy_trigger.finish_attempt

        async def finish_after_vault_cleanup(**kwargs):
            assert not (ws / "secrets/default_vault.yml").exists(), "clean the shared placeholder before releasing the lock"
            return await finish(**kwargs)

        monkeypatch.setattr(deploy_trigger, "finish_attempt", finish_after_vault_cleanup)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            response = await client.post("/v1/deployments/dep-1/attempts", json={"scope": "full"})
            assert response.status_code == 201, response.text
            await asyncio.gather(*list(deploy_trigger._BACKGROUND_TASKS))
            attempt = (await client.get("/v1/deployments/dep-1/attempts")).json()["items"][0]
        assert seen["extravars"].get("proxmox_api_token_secret") == "test-proxmox-secret"
        assert seen["extravars"].get("proxmox_api_user") == "deployer@pve"
        assert not (ws / "secrets/default_vault.yml").exists()
        assert attempt["state"] == ("failed" if start_fails else "succeeded")
        assert "test-proxmox-secret" not in (ws / "events.jsonl").read_text()
    finally:
        await asyncio.gather(*list(deploy_trigger._BACKGROUND_TASKS), return_exceptions=True)
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_attempt_checks_current_network_state_before_spawning_runner(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    from app.core import deploy_trigger, scenario_networks
    from app.core.preflight import PreflightCheck
    calls = []

    async def blocked(scenario_dir, host, *, scope="full"):
        calls.append((scenario_dir, host.id, scope))
        return [PreflightCheck(check="scenario_networks", result="block", detail="SDN collision")]

    # Accommodate either module-qualified calls or a direct function import.
    monkeypatch.setattr(scenario_networks, "check_scenario_networks", blocked)
    monkeypatch.setattr(deploy_trigger, "check_scenario_networks", blocked, raising=False)
    monkeypatch.setenv("RANGE42_AUTO_START_ATTEMPTS", "1")
    try:
        ws, _ = await seed_scenario(dbmod, tmp_path, vmids=())
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            response = await client.post("/v1/deployments/dep-1/attempts", json={"scope": "configure"})
            assert response.status_code == 201, response.text
            assert response.json()["state"] == "failed"
            assert response.json()["sub_reason"] == "PREFLIGHT_BLOCKED"
        assert calls and calls[0][1:] == ("h", "configure")
        assert calls[0][0].is_relative_to(ws / "runner")
        assert not (ws / "secrets/default_vault.yml").exists()
    finally:
        await asyncio.gather(*list(deploy_trigger._BACKGROUND_TASKS), return_exceptions=True)
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_attempt_rechecks_vm_ownership_before_configuration_spawn(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    from app.core import deploy_trigger, scenario_resources
    from app.core.preflight import PreflightCheck
    calls = []

    async def blocked(scenario_dir, host, *, deployment_id, scope="full"):
        calls.append((scenario_dir, host.id, deployment_id, scope))
        return [PreflightCheck(check="scenario_resources", result="block", code="VM_OWNERSHIP_MISMATCH",
                               detail="VMID now belongs to a different deployment")]

    monkeypatch.setattr(scenario_resources, "check_scenario_resources", blocked)
    monkeypatch.setattr(deploy_trigger, "check_scenario_resources", blocked, raising=False)
    monkeypatch.setenv("RANGE42_AUTO_START_ATTEMPTS", "1")
    try:
        ws, _ = await seed_scenario(dbmod, tmp_path, vmids=())
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            response = await client.post("/v1/deployments/dep-1/attempts", json={"scope": "configure"})
            assert response.status_code == 201, response.text
            assert response.json()["state"] == "failed"
            assert response.json()["sub_reason"] == "PREFLIGHT_BLOCKED"
        assert calls and calls[0][1:] == ("h", "dep-1", "configure")
        assert calls[0][0].is_relative_to(ws / "runner")
        assert not (ws / "secrets/default_vault.yml").exists()
    finally:
        await asyncio.gather(*list(deploy_trigger._BACKGROUND_TASKS), return_exceptions=True)
        await dbmod.dispose_engine()
