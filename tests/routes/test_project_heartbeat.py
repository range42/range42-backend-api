"""POST /v1/projects/{id}/heartbeat — the endpoint the UI already polls (#106).

`ProjectRepoAdapter.startHeartbeatWorker` points a SharedWorker at this path
on a timer. With no route it 404'd every tick, so after the stale threshold
every open editor was told its session had gone stale.
"""
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


async def _seed_project(dbmod, project_id="p1"):
    from app.core.models import Project, Source
    async with dbmod.get_session_factory()() as s:
        s.add(Source(id="s1", provider="github",
                     base_url="https://github.com", auth_kind="none"))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Project(id=project_id, name="proj", source_id="s1",
                      branch_strategy="shared_repo_subdir"))
        await s.commit()


@pytest.mark.asyncio
async def test_heartbeat_returns_204_for_known_project(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    await _seed_project(dbmod)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.post("/v1/projects/p1/heartbeat")
    assert r.status_code == 204
    assert r.content == b""


@pytest.mark.asyncio
async def test_heartbeat_404s_for_unknown_project(tmp_path, monkeypatch):
    app, _ = await _boot(tmp_path, monkeypatch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.post("/v1/projects/nope/heartbeat")
    assert r.status_code == 404
    assert r.json()["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_heartbeat_is_idempotent_and_writes_nothing(tmp_path, monkeypatch):
    """It runs on a short interval per open editor — it must stay cheap."""
    app, dbmod = await _boot(tmp_path, monkeypatch)
    await _seed_project(dbmod)
    from app.core.models import Project
    from sqlalchemy import select

    async with dbmod.get_session_factory()() as s:
        before = (await s.execute(select(Project).where(Project.id == "p1"))).scalar_one()
        snapshot = (before.name, before.source_id, before.branch_strategy)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        for _ in range(5):
            assert (await c.post("/v1/projects/p1/heartbeat")).status_code == 204

    async with dbmod.get_session_factory()() as s:
        after = (await s.execute(select(Project).where(Project.id == "p1"))).scalar_one()
        assert (after.name, after.source_id, after.branch_strategy) == snapshot
