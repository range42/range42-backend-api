"""/v1/deployments CRUD + attempts tests."""
import pytest
from httpx import ASGITransport, AsyncClient


async def _boot(tmp_path, monkeypatch):
    monkeypatch.setenv("RANGE42_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    monkeypatch.setenv("RANGE42_WORKSPACE_ROOT", str(tmp_path))
    from importlib import reload
    from app.core import config as cfg
    reload(cfg)
    import app.core.db as dbmod
    reload(dbmod)
    from app.core.models import Base
    engine = dbmod.get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from app.main import create_app
    return create_app(), dbmod


@pytest.mark.asyncio
async def test_create_deployment_scaffolds_workspace(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t", follow_redirects=True) as c:
            r = await c.post("/v1/catalog/sources", json={
                "provider": "github", "base_url": "https://g.com",
                "auth_kind": "none", "token_ref": None})
            assert r.status_code == 201, r.text
            sid = r.json()["id"]
            r = await c.post("/v1/projects", json={
                "name": "p", "source_id": sid, "branch_strategy": "shared_repo_subdir"})
            assert r.status_code == 201, r.text
            pid = r.json()["id"]
            r = await c.post("/v1/proxmox/hosts", json={
                "name": "pve01", "api_url": "https://pve01:8006",
                "node_name": "pve01", "token_ref": "r@p!t=v",
                "default_bridge": "vmbr0"})
            assert r.status_code == 201, r.text
            hid = r.json()["id"]
            r = await c.post("/v1/deployments", json={
                "codename": "ALPHA", "scenario_label": "demo",
                "project_id": pid, "target_host_id": hid,
                "team_count": 2,
            })
            assert r.status_code == 201, r.text
            dep = r.json()
            assert dep["state"] == "pending"
            assert dep["workspace_path"].endswith("ALPHA-demo")

            # Workspace dirs are scaffolded.
            from pathlib import Path
            ws = Path(dep["workspace_path"])
            assert ws.is_dir()
            for sub in ("inventory", "secrets", "ssh_keys", "bin", "runner"):
                assert (ws / sub).is_dir()
            assert (ws / "events.jsonl").exists()

            # Attempt CRUD.
            r = await c.post(f"/v1/deployments/{dep['id']}/attempts",
                             json={"scope": "full"})
            assert r.status_code == 201, r.text
            assert r.json()["scope"] == "full"
            assert r.json()["state"] == "pending"
            r = await c.get(f"/v1/deployments/{dep['id']}/attempts")
            assert r.status_code == 200
            assert len(r.json()["items"]) == 1

            # Get deployment single.
            r = await c.get(f"/v1/deployments/{dep['id']}")
            assert r.status_code == 200
            assert r.json()["current_attempt_id"] is not None

            # List paginated.
            r = await c.get("/v1/deployments")
            assert r.status_code == 200
            assert r.json()["total"] == 1
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_get_deployment_not_found(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t", follow_redirects=True) as c:
            r = await c.get("/v1/deployments/bogus")
            assert r.status_code == 404
            assert r.json()["code"] == "NOT_FOUND"
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_create_attempt_on_missing_deployment(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t", follow_redirects=True) as c:
            r = await c.post("/v1/deployments/bogus/attempts", json={"scope": "full"})
            assert r.status_code == 404
    finally:
        await dbmod.dispose_engine()
