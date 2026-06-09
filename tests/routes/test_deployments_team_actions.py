"""Per-team reset, snapshot, rollback, cancel tests."""
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


async def _seed(dbmod, tmp_path, team_count=3, state="completed"):
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
    async with dbmod.get_session_factory()() as s:
        s.add(Deployment(id="dep-1", codename="A", scenario_label="b",
                         project_id="p", target_host_id="h",
                         team_count=team_count, state=state,
                         workspace_path=str(tmp_path / "A-b")))
        await s.commit()


@pytest.mark.asyncio
async def test_team_reset_enqueues_attempt(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        await _seed(dbmod, tmp_path, team_count=3)
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as c:
            r = await c.post("/v1/deployments/dep-1/teams/2/reset")
            assert r.status_code == 202, r.text
            assert r.json()["scope"] == "team_reset"
            assert r.json()["team_id"] == 2

            r = await c.post("/v1/deployments/dep-1/teams/99/reset")
            assert r.status_code == 400
            assert r.json()["code"] == "TEAM_OUT_OF_RANGE"

            r = await c.post("/v1/deployments/dep-1/teams/0/reset")
            assert r.status_code == 400
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_snapshot_enqueues_attempt(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        await _seed(dbmod, tmp_path)
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as c:
            r = await c.post("/v1/deployments/dep-1/snapshot",
                             json={"scope": "all"})
            assert r.status_code == 202, r.text
            assert r.json()["scope"] == "snapshot_all"

            r = await c.post("/v1/deployments/dep-1/snapshot",
                             json={"scope": "team", "team_id": 1})
            assert r.status_code == 202
            assert r.json()["scope"] == "snapshot_team"
            assert r.json()["team_id"] == 1
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_rollback_refuses_when_no_snapshot(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        await _seed(dbmod, tmp_path)
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as c:
            r = await c.post("/v1/deployments/dep-1/rollback",
                             json={"scope": "all"})
            assert r.status_code == 409
            assert r.json()["code"] == "SNAPSHOT_MISSING"
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_rollback_refuses_when_snapshot_expired(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        await _seed(dbmod, tmp_path)
        from app.core.models import Snapshot
        async with dbmod.get_session_factory()() as s:
            s.add(Snapshot(id="sn-1", deployment_id="dep-1", vm_id=201,
                           team_id=1, name="s", kind="manual", expired=True))
            await s.commit()
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as c:
            r = await c.post("/v1/deployments/dep-1/rollback",
                             json={"scope": "team", "team_id": 1})
            assert r.status_code == 409
            assert r.json()["code"] == "SNAPSHOT_EXPIRED"
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_rollback_accepts_when_snapshot_present(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        await _seed(dbmod, tmp_path)
        from app.core.models import Snapshot
        async with dbmod.get_session_factory()() as s:
            s.add(Snapshot(id="sn-1", deployment_id="dep-1", vm_id=201,
                           team_id=1, name="s", kind="manual", expired=False))
            await s.commit()
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as c:
            r = await c.post("/v1/deployments/dep-1/rollback",
                             json={"scope": "team", "team_id": 1})
            assert r.status_code == 202, r.text
            assert r.json()["scope"] == "rollback_team_1"

            r = await c.get("/v1/deployments/dep-1/snapshots")
            assert r.status_code == 200
            assert r.json()["total"] == 1
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_cancel_without_inflight_attempt(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        await _seed(dbmod, tmp_path)
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as c:
            r = await c.post("/v1/deployments/dep-1/cancel")
            assert r.status_code == 409
            assert r.json()["code"] == "NO_INFLIGHT_ATTEMPT"
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_cancel_with_running_attempt_marks_cancelled(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        await _seed(dbmod, tmp_path)
        from datetime import datetime, timezone
        from app.core.models import Attempt, Deployment
        async with dbmod.get_session_factory()() as s:
            att = Attempt(id="att-1", deployment_id="dep-1", scope="full",
                          state="running",
                          started_at=datetime.now(timezone.utc))
            s.add(att)
            dep = await s.get(Deployment, "dep-1")
            dep.current_attempt_id = "att-1"
            await s.commit()
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as c:
            r = await c.post("/v1/deployments/dep-1/cancel")
            assert r.status_code == 202, r.text
            body = r.json()
            assert body["state"] == "cancelled"
            assert body["ended_at"] is not None
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_cancel_refuses_terminal_attempt(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        await _seed(dbmod, tmp_path)
        from datetime import datetime, timezone
        from app.core.models import Attempt, Deployment
        async with dbmod.get_session_factory()() as s:
            att = Attempt(id="att-1", deployment_id="dep-1", scope="full",
                          state="succeeded",
                          started_at=datetime.now(timezone.utc))
            s.add(att)
            dep = await s.get(Deployment, "dep-1")
            dep.current_attempt_id = "att-1"
            await s.commit()
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as c:
            r = await c.post("/v1/deployments/dep-1/cancel")
            assert r.status_code == 409
            assert r.json()["code"] == "ATTEMPT_TERMINAL"
    finally:
        await dbmod.dispose_engine()
