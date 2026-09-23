import asyncio
import shutil

import pytest
from httpx import ASGITransport, AsyncClient

from tests.core.test_native_contexts import context
from tests.core.test_native_scenarios import scenario
from tests.routes.test_deployments_preflight import _boot, _seed


@pytest.mark.asyncio
async def test_native_context_preview_create_preflight_and_attempt_round_trip(tmp_path_factory, monkeypatch):
    from app.core import scenario as scenario_module
    from app.core import deploy_trigger
    from app.core.models import ProxmoxHost, Attempt
    from app.core.preflight import PreflightCheck
    from app.routes.v1.deployments import preflight as preflight_module
    from app.routes.v1.deployments import crud
    from app.core import config
    tmp_path = tmp_path_factory.mktemp("native-api")  # Unix agent sockets have a 108-byte pathname limit.
    context(tmp_path, monkeypatch)
    repo = tmp_path / "source-repo"
    scenario(repo)
    app, db = await _boot(tmp_path, monkeypatch)
    monkeypatch.setattr(crud, "settings", config.settings)
    monkeypatch.setenv("RANGE42_AUTO_START_ATTEMPTS", "0")
    monkeypatch.setattr(deploy_trigger, "_decrypt_vault", lambda *_: {}, raising=False)
    def checkout(**kwargs):
        shutil.copytree(repo, kwargs["dest"])
        return kwargs["dest"]
    monkeypatch.setattr(scenario_module, "checkout_repository", checkout)
    async def reachable(*args):
        return PreflightCheck(check="proxmox_api", result="pass")
    monkeypatch.setattr(preflight_module, "check_proxmox_api_status", reachable)
    try:
        await _seed(db, tmp_path, repo_owner="range42", repo_name="playbooks")
        async with db.get_session_factory()() as session:
            host = await session.get(ProxmoxHost, "h")
            host.api_url = "https://192.0.2.10:8006"
            await session.commit()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            contexts = await client.get("/v1/contexts")
            assert contexts.status_code == 200, contexts.text
            assert contexts.json()["items"][0]["target_host_id"] == "h"
            preview = await client.get("/v1/projects/p/native-scenario", params={"path": "training/exercise-a", "sha": "a" * 40})
            assert preview.status_code == 200, preview.text
            assert preview.json()["actions"]["full"] == "exercise.setup.sh"
            created = await client.post("/v1/deployments/", json={"project_id": "p", "codename": "NATIVE",
                "scenario_label": "exercise-a", "target_host_id": "h", "team_count": 1, "project_sha": "a" * 40,
                "native": {"path": "training/exercise-a", "context_id": "lab-demo", "features": {"WAZUH": True}}})
            assert created.status_code == 201, created.text
            deployment = created.json()
            assert deployment["native"]["descriptor"]["actions"]["teardown"] == "exercise.delete_all.sh"
            ident = deployment["id"]
            report = await client.post(f"/v1/deployments/{ident}/preflight", json={"scope": "full"})
            assert report.status_code == 200, report.text
            assert report.json()["result"] == "warn"
            assert any(row["check"] == "native_context" for row in report.json()["checks"])
            refused = await client.post(f"/v1/deployments/{ident}/attempts", json={"scope": "reset"})
            assert refused.status_code == 400, refused.text
            attempt = await client.post(f"/v1/deployments/{ident}/attempts", json={"scope": "full"})
            assert attempt.status_code == 201, attempt.text
            captured = {}
            class Handle:
                pid = None
                async def wait(self): return 0
                async def kill(self): pass
            class Runner:
                async def start(self, **kwargs):
                    captured.update(kwargs)
                    return Handle()
            async with db.get_session_factory()() as session:
                row = await session.get(Attempt, attempt.json()["id"])
                await deploy_trigger.start_attempt(session, attempt=row, runner=Runner())
            assert "r42_native_command" in captured["extravars"]
            assert "proxmox_api_token_secret" not in captured["extravars"]
            assert captured["envvars"].get("SSH_AUTH_SOCK")
            await asyncio.gather(*list(deploy_trigger._BACKGROUND_TASKS))
    finally:
        await asyncio.gather(*list(deploy_trigger._BACKGROUND_TASKS), return_exceptions=True)
        await db.dispose_engine()
