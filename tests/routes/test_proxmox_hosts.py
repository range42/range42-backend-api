"""/v1/proxmox/hosts CRUD + health probe tests."""
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
async def test_create_and_delete_host(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post(
                "/v1/proxmox/hosts",
                json={
                    "name": "pve01",
                    "api_url": "https://pve01:8006",
                    "node_name": "pve01",
                    "token_ref": "r42@pam!tok=abc",
                    "default_bridge": "vmbr0",
                },
            )
            assert r.status_code == 201, r.text
            hid = r.json()["id"]
            r = await c.get("/v1/proxmox/hosts")
            assert r.status_code == 200
            assert any(h["id"] == hid for h in r.json()["items"])
            r = await c.delete(f"/v1/proxmox/hosts/{hid}")
            assert r.status_code == 204
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_delete_unknown_host_returns_canonical_envelope(
    tmp_path, monkeypatch
):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.delete("/v1/proxmox/hosts/missing-host")
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
async def test_health_unknown_host_returns_canonical_envelope(
    tmp_path, monkeypatch
):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.get("/v1/proxmox/hosts/bogus/health")
            assert r.status_code == 404
            assert r.json()["code"] == "NOT_FOUND"
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_health_unreachable_host_returns_unreachable_status(
    tmp_path, monkeypatch
):
    """When the Proxmox endpoint cannot be contacted (unreachable host in the
    default IANA 203.0.113.0/24 TEST-NET-3 range is used here via a
    localhost port that is almost certainly closed), the probe records
    status=unreachable and still returns 200 with the HostHealth payload.
    """
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post(
                "/v1/proxmox/hosts",
                json={
                    "name": "pve-off",
                    "api_url": "http://127.0.0.1:1",
                    "node_name": "pve01",
                    "token_ref": "r42@pam!tok=zzz",
                    "default_bridge": "vmbr0",
                },
            )
            hid = r.json()["id"]
            r = await c.get(f"/v1/proxmox/hosts/{hid}/health")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["status"] == "unreachable"
    finally:
        await dbmod.dispose_engine()
