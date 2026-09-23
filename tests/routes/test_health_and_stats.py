import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app


@pytest.mark.asyncio
async def test_health_live_ready_stats(tmp_path, monkeypatch):
    monkeypatch.setenv("RANGE42_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    monkeypatch.setenv("RANGE42_WORKSPACE_ROOT", str(tmp_path))
    from importlib import reload
    from app.core import config as cfg, db as dbmod
    reload(cfg)
    reload(dbmod)
    from app.core.models import Base
    engine = dbmod.get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/v1/health")
        assert r.status_code == 200 and r.json()["status"] == "ok"
        r = await c.get("/v1/health/ready")
        assert r.status_code == 200
        assert r.json()["checks"]["sqlite_wal"]["ok"] is True
        r = await c.get("/v1/admin/stats")
        assert r.status_code == 200 and "open_streams" in r.json()
