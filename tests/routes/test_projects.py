"""/v1/projects CRUD + compose/validate tests."""
import pytest
from httpx import ASGITransport, AsyncClient


async def _boot(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("RANGE42_DB_URL", f"sqlite+aiosqlite:///{db}")
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
async def test_create_and_list_project(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post(
                "/v1/catalog/sources",
                json={
                    "provider": "github",
                    "base_url": "https://github.com",
                    "auth_kind": "none",
                    "token_ref": None,
                },
            )
            sid = r.json()["id"]
            r = await c.post(
                "/v1/projects/",
                json={
                    "name": "p1",
                    "source_id": sid,
                    "branch_strategy": "shared_repo_subdir",
                },
            )
            assert r.status_code == 201, r.text
            pid = r.json()["id"]
            r = await c.get("/v1/projects/")
            assert r.status_code == 200
            assert any(x["id"] == pid for x in r.json()["items"])
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_patch_unknown_project_returns_canonical_envelope(
    tmp_path, monkeypatch
):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.patch(
                "/v1/projects/nope",
                json={
                    "name": "x",
                    "source_id": "s",
                    "branch_strategy": "shared_repo_subdir",
                },
            )
            assert r.status_code == 404
            body = r.json()
            assert set(body.keys()) >= {
                "error",
                "message",
                "code",
                "details",
                "trace_id",
                "timestamp",
            }
            assert body["code"] == "NOT_FOUND"
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_compose_unknown_project_returns_not_found(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post("/v1/projects/missing/compose", json={})
            assert r.status_code == 404
            assert r.json()["code"] == "NOT_FOUND"
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_compose_incomplete_project_surfaces_project_not_pinned(
    tmp_path, monkeypatch
):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post(
                "/v1/catalog/sources",
                json={
                    "provider": "github",
                    "base_url": "https://github.com",
                    "auth_kind": "none",
                },
            )
            sid = r.json()["id"]
            r = await c.post(
                "/v1/projects/",
                json={
                    "name": "p",
                    "source_id": sid,
                    "branch_strategy": "shared_repo_subdir",
                },
            )
            pid = r.json()["id"]
            r = await c.post(f"/v1/projects/{pid}/compose", json={})
            assert r.status_code == 400
            assert r.json()["code"] == "PROJECT_NOT_PINNED"
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_validate_unknown_project_returns_not_found(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post("/v1/projects/missing/validate")
            assert r.status_code == 404
            assert r.json()["code"] == "NOT_FOUND"
    finally:
        await dbmod.dispose_engine()
