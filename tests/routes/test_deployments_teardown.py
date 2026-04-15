"""DELETE /v1/deployments/:id teardown tests."""
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


async def _seed(dbmod, tmp_path, codename="AURORA", state="completed"):
    from app.core.models import Deployment, Project, ProxmoxHost, Source
    async with dbmod.get_session_factory()() as s:
        s.add(Source(id="s", provider="github", base_url="u", auth_kind="none"))
        s.add(ProxmoxHost(id="h", name="n", api_url="u", node_name="n",
                          token_ref="t"))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Project(id="p", name="p", source_id="s",
                      branch_strategy="shared_repo_subdir"))
        await s.commit()
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    async with dbmod.get_session_factory()() as s:
        s.add(Deployment(id="dep-1", codename=codename, scenario_label="x",
                         project_id="p", target_host_id="h", team_count=1,
                         state=state, workspace_path=str(ws)))
        await s.commit()
    return ws


@pytest.mark.asyncio
async def test_teardown_rejects_wrong_codename(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        await _seed(dbmod, tmp_path, codename="AURORA")
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as c:
            r = await c.request("DELETE", "/v1/deployments/dep-1",
                                json={"confirm_codename": "NOPE"})
            assert r.status_code == 400
            assert r.json()["code"] == "TEARDOWN_CONFIRM_MISMATCH"
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_teardown_accepts_correct_codename(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        ws = await _seed(dbmod, tmp_path, codename="AURORA")
        assert ws.exists()
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as c:
            r = await c.request("DELETE", "/v1/deployments/dep-1",
                                json={"confirm_codename": "AURORA"})
            assert r.status_code == 202, r.text
            att = r.json()
            assert att["scope"] == "teardown"
            assert att["state"] == "pending"
            # Workspace dir is removed.
            assert not ws.exists()
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_teardown_blocked_when_state_unknown(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        await _seed(dbmod, tmp_path, codename="AURORA", state="unknown")
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as c:
            r = await c.request("DELETE", "/v1/deployments/dep-1",
                                json={"confirm_codename": "AURORA"})
            assert r.status_code == 409
            assert r.json()["code"] == "DEPLOYMENT_UNKNOWN_STATE"
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_teardown_missing_deployment(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as c:
            r = await c.request("DELETE", "/v1/deployments/bogus",
                                json={"confirm_codename": "X"})
            assert r.status_code == 404
    finally:
        await dbmod.dispose_engine()
