"""Concrete projects use the same pinned scenario for preflight and execution."""
import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.models import Attempt, Deployment, Project, ProxmoxHost, Source, WorkspaceLock
from app.core.errors import Range42Error
from app.core.preflight import PreflightCheck
from tests.core.test_project_repository import make_repository
from tests.fixtures.fake_runner import FakeRunner
from tests.routes.test_deployments_preflight import _boot as _base_boot, _seed


async def _boot(tmp_path, monkeypatch):
    # Preserve production URL validation; substitute only the Git transport
    # so integration fixtures use a real, local repository without network.
    from app.core import scenario
    from app.core.project import checkout_repository

    def checkout_fixture(*, repo_url, **kwargs):
        assert repo_url == "https://github.com/owner/project.git"
        return checkout_repository(repo_url=(tmp_path / "repos/owner/project.git").as_uri(), **kwargs)

    monkeypatch.setattr(scenario, "checkout_repository", checkout_fixture)
    return await _base_boot(tmp_path, monkeypatch)


async def seed_scenario(dbmod, tmp_path, *, vmids=(5000,), missing=None, subdir="labs/demo", extra_files=None):
    scenario = f"{subdir}/scenarios/content"
    files = {
        f"{scenario}/main.yml": "- import_playbook: configure.yml\n",
        f"{scenario}/configure.yml": "- hosts: guest\n  gather_facts: false\n  tasks: []\n",
        f"{scenario}/hosts.yml": "all:\n  hosts:\n    guest:\n      ansible_connection: local\n",
        f"{scenario}/manifest/scenario_vms.json": json.dumps({
            "scenario": "content", "version": 1,
            "vms": [{"vm_id": vmid} for vmid in vmids],
        }),
        f"{scenario}/files/config.txt": "pinned content",
        f"{scenario}/scripts/setup.sh": "#!/bin/sh\necho content\n",
    }
    if missing:
        files.pop(f"{scenario}/{missing}")
    files.update({f"{scenario}/{name}": value for name, value in (extra_files or {}).items()})
    src = tmp_path / "repos/owner/project.git"
    sha = make_repository(src, files)
    ws = await _seed(dbmod, tmp_path, scenario="content", project_sha=sha,
                     repo_owner="owner", repo_name="project")
    async with dbmod.get_session_factory()() as session:
        source = await session.get(Source, "s")
        source.base_url = "https://github.com"
        project = await session.get(Project, "p")
        project.subdir = subdir
        host = await session.get(ProxmoxHost, "h")
        host.token_ref = "deployer@pve!ui=test-proxmox-secret"
        await session.commit()
    return ws, sha


def healthy_host(monkeypatch):
    from app.routes.v1.deployments import preflight

    async def healthy(*args):
        return PreflightCheck(check="proxmox_api", result="pass")

    monkeypatch.setattr(preflight, "check_proxmox_api_status", healthy)


@pytest.mark.asyncio
async def test_preflight_resolves_pinned_scenario_without_server_installation(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        await seed_scenario(dbmod, tmp_path)
        healthy_host(monkeypatch)
        monkeypatch.delenv("API_BACKEND_WWWAPP_PLAYBOOKS_DIR", raising=False)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            response = await client.post("/v1/deployments/dep-1/preflight")
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["result"] == "pass", report
        assert any(c["check"] == "project_scenario" and c["result"] == "pass" for c in report["checks"])
        assert any(c["check"] == "vmid_collision" for c in report["checks"])
        assert not any(c["check"] in ("resource_budget", "secret_completeness") for c in report["checks"])
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
@pytest.mark.parametrize("vmids", [(100,), (5000, 5000)])
async def test_preflight_checks_actual_manifest_vmids(tmp_path, monkeypatch, vmids):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        await seed_scenario(dbmod, tmp_path, vmids=vmids)
        healthy_host(monkeypatch)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            report = (await client.post("/v1/deployments/dep-1/preflight")).json()
        assert any(c["check"] == "vmid_collision" and c["result"] == "block" for c in report["checks"]), report
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_missing_project_scenario_does_not_fall_back_to_installed_scenario(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        await seed_scenario(dbmod, tmp_path, missing="hosts.yml")
        healthy_host(monkeypatch)
        installed = tmp_path / "installed/scenarios/content/main.yml"
        installed.parent.mkdir(parents=True)
        installed.write_text("- hosts: localhost\n  tasks: []\n")
        monkeypatch.setenv("API_BACKEND_WWWAPP_PLAYBOOKS_DIR", str(tmp_path / "installed"))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            report = (await client.post("/v1/deployments/dep-1/preflight")).json()
        assert report["result"] == "block", report
        assert any(c["code"] == "PROJECT_SCENARIO_INVALID" and "hosts.yml" in c["detail"]
                   for c in report["checks"]), report
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_attempt_runs_pinned_project_playbook_with_its_inventory_and_assets(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    from app.core.deploy_trigger import _BACKGROUND_TASKS, start_attempt

    class RecordingRunner(FakeRunner):
        async def start(self, **kwargs):
            self.arguments = kwargs
            return await super().start(**kwargs)

    runner = RecordingRunner(script=[])
    try:
        ws, sha = await seed_scenario(dbmod, tmp_path)
        monkeypatch.delenv("API_BACKEND_WWWAPP_PLAYBOOKS_DIR", raising=False)
        async with dbmod.get_session_factory()() as session:
            session.add(Attempt(id="attempt-content", deployment_id="dep-1", scope="full", state="pending"))
            await session.commit()
            attempt = (await session.execute(select(Attempt))).scalar_one()
            await start_attempt(session, attempt=attempt, runner=runner)
        await asyncio.gather(*list(_BACKGROUND_TASKS))
        variables = runner.arguments["extravars"]
        playbook = Path(variables["r42_playbook_path"])
        assert playbook.is_relative_to(ws / "runner/attempt-content/checkout")
        assert playbook.read_text() == "- import_playbook: configure.yml\n"
        assert Path(variables["r42_inventory_path"]) == playbook.parent / "hosts.yml"
        assert (playbook.parent / "files/config.txt").read_text() == "pinned content"
        assert (playbook.parent / "scripts/setup.sh").is_file()
        assert runner.arguments["envvars"]["RANGE42_ACTIVE_CONFIG_DIR"] == str(ws)
        async with dbmod.get_session_factory()() as session:
            attempt = await session.get(Attempt, "attempt-content")
            assert attempt.state == "succeeded"
            assert attempt.rc == 0
            assert attempt.ended_at is not None
            assert (await session.execute(select(WorkspaceLock))).scalar_one_or_none() is None
    finally:
        await asyncio.gather(*list(_BACKGROUND_TASKS), return_exceptions=True)
        await dbmod.dispose_engine()


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_api_attempt_executes_real_pinned_content_and_persists_result(tmp_path, monkeypatch, fail):
    binary = Path(sys.executable).parent / "ansible-runner"
    if not binary.is_file() and not shutil.which("ansible-runner"):
        pytest.skip("ansible-runner is not installed")
    monkeypatch.setenv("PATH", f"{binary.parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("RANGE42_AUTO_START_ATTEMPTS", "1")
    config = tmp_path / "ansible.cfg"
    config.write_text("[defaults]\nretry_files_enabled = False\n")
    monkeypatch.setenv("ANSIBLE_CONFIG", str(config))
    app, dbmod = await _boot(tmp_path, monkeypatch)
    from app.core.deploy_trigger import _BACKGROUND_TASKS
    from app.core.events import EventsReader
    tasks = [
        {"name": "Attempt to print guest password", "ansible.builtin.debug": {
            "msg": "sensitive value: {{ default_admin_vm_ci_password }}",
        }},
        {"name": "Copy project content", "ansible.builtin.copy": {
            "src": "files/config.txt", "dest": "{{ r42_workspace_dir }}/copied.txt", "mode": "0600",
        }},
        {"name": "Run project script", "ansible.builtin.script": {
            "cmd": 'scripts/setup.sh "{{ r42_workspace_dir }}/copied.txt" "{{ r42_workspace_dir }}/processed.txt"',
        }},
    ]
    if fail:
        tasks.append({"name": "Intentional verification failure", "ansible.builtin.fail": {"msg": "test failure"}})
    try:
        ws, sha = await seed_scenario(dbmod, tmp_path, vmids=(), extra_files={
            "configure.yml": yaml.safe_dump([{"hosts": "guest", "gather_facts": False, "tasks": tasks}]),
            "scripts/setup.sh": '#!/bin/sh\nset -eu\ntr "[:lower:]" "[:upper:]" < "$1" > "$2"\n',
        })
        # The branch advances after deployment pinning. Execution must retain
        # the exact original file, not whichever content is now at HEAD.
        repo = tmp_path / "repos/owner/project.git"
        (repo / "labs/demo/scenarios/content/files/config.txt").write_text("unreviewed branch change")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.test",
                        "commit", "-qm", "Later content"], cwd=repo, check=True)
        healthy_host(monkeypatch)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            preflight = await client.post("/v1/deployments/dep-1/preflight")
            assert preflight.json()["result"] == "pass", preflight.text
            response = await client.post("/v1/deployments/dep-1/attempts", json={"scope": "full"})
            assert response.status_code == 201, response.text
            assert response.json()["state"] == "deploying", response.text
            attempt_id = response.json()["id"]
            await asyncio.wait_for(asyncio.gather(*list(_BACKGROUND_TASKS)), timeout=30)
            dep = (await client.get("/v1/deployments/dep-1")).json()
            attempts = (await client.get("/v1/deployments/dep-1/attempts")).json()["items"]
        expected = "failed" if fail else "succeeded"
        assert dep["state"] == expected, dep
        assert attempts[0]["state"] == expected
        assert attempts[0]["rc"] == (2 if fail else 0)
        assert (ws / "copied.txt").read_text() == "pinned content"
        assert (ws / "processed.txt").read_text() == "PINNED CONTENT"
        events = list(EventsReader(ws / "events.jsonl").read_range(from_seq=0))
        password = (ws / "secrets/guest_admin_password").read_text().strip()
        assert len(password) >= 32
        assert password not in (ws / "events.jsonl").read_text()
        assert any(event["payload"].get("task_name") == "Attempt to print guest password" for event in events)
        assert len([event for event in events if event["event_type"] == "attempt_end"]) == 1
        assert events[-1]["payload"]["terminal_state"] == expected
        assert any(event["payload"].get("task_name") == "Run project script" for event in events)
        assert attempts[0]["event_cursor_tip"] == events[-1]["event_seq"]
        artifact = ws / "runner" / attempt_id
        for name in ("env/envvars", "env/extravars", "command"):
            assert not (artifact / name).exists()
        async with dbmod.get_session_factory()() as session:
            assert (await session.get(Deployment, "dep-1")).project_sha == sha
            assert (await session.execute(select(WorkspaceLock))).scalar_one_or_none() is None
    finally:
        await asyncio.gather(*list(_BACKGROUND_TASKS), return_exceptions=True)
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_cancelling_background_monitor_stops_runner_and_releases_workspace(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    from app.core.deploy_trigger import _BACKGROUND_TASKS, start_attempt

    class PausedHandle:
        pid = None
        rc = None
        killed = False

        async def wait(self):
            await asyncio.Event().wait()

        async def kill(self):
            self.killed = True

    handle = PausedHandle()

    class PausedRunner:
        async def start(self, **kwargs):
            return handle

    try:
        await seed_scenario(dbmod, tmp_path)
        async with dbmod.get_session_factory()() as session:
            attempt = Attempt(id="cancel-me", deployment_id="dep-1", scope="full", state="pending")
            session.add(attempt)
            await session.commit()
            await start_attempt(session, attempt=attempt, runner=PausedRunner())
        await asyncio.sleep(0)
        tasks = list(_BACKGROUND_TASKS)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        assert handle.killed
        async with dbmod.get_session_factory()() as session:
            assert (await session.get(Attempt, "cancel-me")).state == "cancelled"
            assert (await session.execute(select(WorkspaceLock))).scalar_one_or_none() is None
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_direct_trigger_rejects_unsupported_concrete_scope(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    from app.core.deploy_trigger import _BACKGROUND_TASKS, start_attempt
    try:
        await seed_scenario(dbmod, tmp_path)
        async with dbmod.get_session_factory()() as session:
            attempt = Attempt(id="wrong-scope", deployment_id="dep-1", scope="rollback_all", state="pending")
            session.add(attempt)
            await session.commit()
            with pytest.raises(Range42Error) as exc:
                await start_attempt(session, attempt=attempt, runner=FakeRunner())
            assert exc.value.code == "PROJECT_SCENARIO_SCOPE_UNSUPPORTED"
    finally:
        await asyncio.gather(*list(_BACKGROUND_TASKS), return_exceptions=True)
        await dbmod.dispose_engine()
