"""/v1/deployments/:id/preflight tests."""
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


async def _seed(dbmod, tmp_path, codename="X", scenario="y"):
    from app.core.models import Deployment, Project, ProxmoxHost, Source
    async with dbmod.get_session_factory()() as s:
        s.add(Source(id="s", provider="github", base_url="u", auth_kind="none"))
        s.add(ProxmoxHost(id="h", name="n", api_url="http://127.0.0.1:1",
                          node_name="n", token_ref="t"))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Project(id="p", name="p", source_id="s",
                      branch_strategy="shared_repo_subdir"))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Deployment(id="dep-1", codename=codename, scenario_label=scenario,
                         project_id="p", target_host_id="h", team_count=1,
                         state="pending", workspace_path=str(tmp_path / f"{codename}-{scenario}")))
        await s.commit()


@pytest.mark.asyncio
async def test_preflight_round_trip(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        await _seed(dbmod, tmp_path)
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as c:
            r = await c.post("/v1/deployments/dep-1/preflight")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["result"] in ("pass", "warn", "block")
            assert isinstance(body["checks"], list)
            assert any(c["check"] == "proxmox_api" for c in body["checks"])
            r = await c.get("/v1/deployments/dep-1/preflight")
            assert r.status_code == 200
            assert r.json()["deployment_id"] == "dep-1"
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_preflight_get_404_when_no_record(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        await _seed(dbmod, tmp_path)
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as c:
            r = await c.get("/v1/deployments/dep-1/preflight")
            assert r.status_code == 404
            assert r.json()["code"] == "NOT_FOUND"
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_preflight_post_404_on_missing_deployment(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as c:
            r = await c.post("/v1/deployments/bogus/preflight")
            assert r.status_code == 404
    finally:
        await dbmod.dispose_engine()
