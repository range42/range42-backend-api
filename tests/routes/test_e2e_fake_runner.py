"""E2E smoke — catalog + project + host + deployment + FakeRunner + events.

Drives the full /v1 surface in one test: registers a git source, creates a
project, registers a Proxmox host, creates a deployment (which scaffolds
the workspace), enqueues an attempt, spawns FakeRunner via deploy_trigger,
and verifies events.jsonl receives attempt_start.

Satisfies the integration tier of spec section 10.
"""
import asyncio
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from tests.fixtures.fake_runner import FakeRunner


@pytest.mark.asyncio
async def test_full_flow_catalog_project_deploy_sse(tmp_path, monkeypatch):
    monkeypatch.setenv("RANGE42_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    monkeypatch.setenv("RANGE42_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("RANGE42_AUTO_START_ATTEMPTS", "0")
    from importlib import reload
    from app.core import config as cfg, db as dbmod
    reload(cfg)
    reload(dbmod)
    from app.core.models import Base
    engine = dbmod.get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from app.main import create_app
    app = create_app()
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t",
                               follow_redirects=True) as c:
            sid = (await c.post("/v1/catalog/sources", json={
                "provider": "github", "base_url": "https://g.com",
                "auth_kind": "none", "token_ref": None})).json()["id"]
            pid = (await c.post("/v1/projects", json={
                "name": "p", "source_id": sid,
                "branch_strategy": "shared_repo_subdir"})).json()["id"]
            hid = (await c.post("/v1/proxmox/hosts", json={
                "name": "pve01", "api_url": "https://pve01:8006",
                "node_name": "pve01", "token_ref": "t",
                "default_bridge": "vmbr0"})).json()["id"]
            dep = (await c.post("/v1/deployments", json={
                "codename": "AURORA", "scenario_label": "demo",
                "project_id": pid, "target_host_id": hid,
                "team_count": 1})).json()
            att = (await c.post(f"/v1/deployments/{dep['id']}/attempts",
                                json={"scope": "full"})).json()

        from app.core.deploy_trigger import start_attempt
        from app.core.models import Attempt
        runner = FakeRunner(script=[
            {"event_type": "phase_transition",
             "payload": {"from": None, "to": "network"}},
            {"event_type": "task_end",
             "payload": {"task_name": "create_vm", "result": "ok"}},
        ])
        async with dbmod.get_session_factory()() as s:
            a = (await s.execute(
                select(Attempt).where(Attempt.id == att["id"]))).scalar_one()
            await start_attempt(s, attempt=a, runner=runner)
        await asyncio.sleep(0.3)
        ws = Path(dep["workspace_path"])
        body = (ws / "events.jsonl").read_text()
        assert "attempt_start" in body
    finally:
        await dbmod.dispose_engine()
