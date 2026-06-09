"""/v1/catalog/sources CRUD + refresh tests using httpx ASGITransport."""
import pytest
from httpx import ASGITransport, AsyncClient


async def _boot(tmp_path, monkeypatch):
    """Common setup: isolated sqlite, fresh engine/session factory, tables created."""
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
    app = create_app()
    return app, dbmod


@pytest.mark.asyncio
async def test_source_crud_roundtrip(tmp_path, monkeypatch):
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
            assert r.status_code == 201, r.text
            sid = r.json()["id"]
            r = await c.get("/v1/catalog/sources")
            assert r.status_code == 200
            body = r.json()
            assert any(s["id"] == sid for s in body["items"])
            assert body["total"] >= 1
            r = await c.delete(f"/v1/catalog/sources/{sid}")
            assert r.status_code == 204
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_delete_unknown_source_returns_canonical_envelope(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.delete("/v1/catalog/sources/does-not-exist")
            assert r.status_code == 404
            body = r.json()
            # Envelope from app.core.errors: error, message, code, details, trace_id, timestamp
            assert set(body.keys()) >= {"error", "message", "code", "details", "trace_id", "timestamp"}
            assert body["code"] == "NOT_FOUND"
            assert body["error"] == "not_found"
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_refresh_unknown_source_returns_not_found(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post("/v1/catalog/sources/nope/refresh")
            assert r.status_code == 404
            body = r.json()
            assert body["code"] == "NOT_FOUND"
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_refresh_source_with_no_repos_is_ok(tmp_path, monkeypatch):
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
            r = await c.post(f"/v1/catalog/sources/{sid}/refresh")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["source_id"] == sid
            assert body["repos_seen"] == 0
            assert body["entries_indexed"] == 0
    finally:
        await dbmod.dispose_engine()
