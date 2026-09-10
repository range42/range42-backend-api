"""Configure can apply a new content revision without changing provisioned targets."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.errors import Range42Error
from app.core.models import Deployment
from app.core.scenario import prepare_project_scenario
from tests.routes.test_project_scenario_execution import _boot, seed_scenario


def advance(tmp_path, changes):
    repo = tmp_path / "repos/owner/project.git"
    for name, content in changes.items():
        path = repo / "labs/demo/scenarios/content" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if content is None:
            path.unlink()
        else:
            path.write_text(content)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.test",
                    "commit", "-qm", "Edited content"], cwd=repo, check=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()


@pytest.mark.asyncio
async def test_configure_runs_new_content_and_preserves_deployment_pin(tmp_path, monkeypatch):
    monkeypatch.setenv("RANGE42_AUTO_START_ATTEMPTS", "1")
    monkeypatch.setenv("PATH", f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}")
    cfg = tmp_path / "ansible.cfg"
    cfg.write_text("[defaults]\nretry_files_enabled=False\n")
    monkeypatch.setenv("ANSIBLE_CONFIG", str(cfg))
    app, dbmod = await _boot(tmp_path, monkeypatch)
    from app.core.deploy_trigger import _BACKGROUND_TASKS
    try:
        ws, baseline = await seed_scenario(dbmod, tmp_path, vmids=())
        candidate = advance(tmp_path, {"configure.yml": """- hosts: guest
  gather_facts: false
  tasks:
    - ansible.builtin.copy:
        content: edited content
        dest: '{{ r42_workspace_dir }}/new-content.txt'
        mode: '0600'
"""})
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            response = await client.post("/v1/deployments/dep-1/attempts", json={
                "scope": "configure", "project_sha": candidate,
            })
            assert response.status_code == 201, response.text
            assert response.json().get("project_sha") == candidate
            await asyncio.wait_for(asyncio.gather(*list(_BACKGROUND_TASKS)), timeout=30)
            attempt = (await client.get("/v1/deployments/dep-1/attempts")).json()["items"][0]
            deployment = (await client.get("/v1/deployments/dep-1")).json()
        assert attempt["state"] == "succeeded", attempt
        assert attempt["project_sha"] == candidate
        assert deployment["project_sha"] == baseline
        assert (ws / "new-content.txt").read_text() == "edited content"
    finally:
        await asyncio.gather(*list(_BACKGROUND_TASKS), return_exceptions=True)
        await dbmod.dispose_engine()


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"manifest/scenario_vms.json": json.dumps({"vms": [{"vm_id": 5001}]})},
    {"manifest/scenario_networks.json": json.dumps({"mode": "existing_bridge", "bridges": ["vmbr0"]})},
    {"hosts.yml": "all:\n  hosts:\n    other:\n      ansible_host: 192.0.2.2\n"},
])
async def test_configure_revision_rejects_changed_targets(tmp_path, monkeypatch, changes):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        ws, baseline = await seed_scenario(dbmod, tmp_path)
        candidate = advance(tmp_path, changes)
        async with dbmod.get_session_factory()() as session:
            dep = await session.get(Deployment, "dep-1")
            with pytest.raises(Range42Error) as error:
                await prepare_project_scenario(session, dep, dest=ws / "candidate", scope="configure", project_sha=candidate)
            assert error.value.code == "PROJECT_CONFIGURATION_TOPOLOGY_CHANGED"
            assert dep.project_sha == baseline
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_configure_revision_rejects_removed_network_manifest(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        ws, _ = await seed_scenario(dbmod, tmp_path, extra_files={
            "manifest/scenario_networks.json": '{"mode":"existing_bridge","bridges":["vmbr0"]}',
        })
        candidate = advance(tmp_path, {"manifest/scenario_networks.json": None})
        async with dbmod.get_session_factory()() as session:
            dep = await session.get(Deployment, "dep-1")
            with pytest.raises(Range42Error) as error:
                await prepare_project_scenario(session, dep, dest=ws / "candidate", scope="configure", project_sha=candidate)
            assert error.value.code == "PROJECT_CONFIGURATION_TOPOLOGY_CHANGED"
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_configure_revision_allows_equivalent_manifest_serialization(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        ws, _ = await seed_scenario(dbmod, tmp_path)
        candidate = advance(tmp_path, {"manifest/scenario_vms.json": '{"version":1,"vms":[{"vm_id":5000}],"scenario":"content"}\n'})
        async with dbmod.get_session_factory()() as session:
            dep = await session.get(Deployment, "dep-1")
            scenario = await prepare_project_scenario(session, dep, dest=ws / "candidate", scope="configure", project_sha=candidate)
            assert scenario.playbook.name == "configure.yml"
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["full", "teardown"])
async def test_revision_override_is_configure_only_before_reservation(tmp_path, monkeypatch, scope):
    monkeypatch.setenv("RANGE42_AUTO_START_ATTEMPTS", "0")
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        _, baseline = await seed_scenario(dbmod, tmp_path)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            response = await client.post("/v1/deployments/dep-1/attempts", json={"scope": scope, "project_sha": baseline})
            assert response.status_code == 400, response.text
            assert response.json()["code"] == "PROJECT_SCENARIO_REVISION_UNSUPPORTED"
            assert (await client.get("/v1/deployments/dep-1/attempts")).json()["total"] == 0
    finally:
        await dbmod.dispose_engine()
