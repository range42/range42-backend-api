"""/v1/proxmox/hosts/{id}/vms/{vmid}/snapshots — per-VM PVE snapshots."""
import json

import httpx
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


class _FakeResp:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data

    def json(self):
        return {"data": self._data}

    @property
    def text(self):
        return json.dumps(self._data)


class _FakeProxmox:
    calls: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None, params=None):
        _FakeProxmox.calls.append(("GET", url, params))
        if url.endswith("/snapshot"):
            return _FakeResp(200, [
                {"name": "base", "description": "first", "snaptime": 1700000000},
                {"name": "current", "description": "You are here!"},
            ])
        return _FakeResp(404, [])

    async def post(self, url, headers=None, data=None):
        _FakeProxmox.calls.append(("POST", url, data))
        return _FakeResp(200, "UPID:pve01:0001:snap::")

    async def delete(self, url, headers=None, params=None):
        _FakeProxmox.calls.append(("DELETE", url, params))
        return _FakeResp(200, "UPID:pve01:0002:delsnap::")


async def _create_host(c):
    r = await c.post("/v1/proxmox/hosts", json={
        "name": "pve01", "api_url": "https://pve01:8006/", "node_name": "pve01",
        "token_ref": "root@pam!tok=secret", "default_bridge": "vmbr0",
    })
    return r.json()["id"]


@pytest.mark.asyncio
async def test_list_snapshots_filters_current(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeProxmox)
    _FakeProxmox.calls = []
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            hid = await _create_host(c)
            r = await c.get(f"/v1/proxmox/hosts/{hid}/vms/200/snapshots")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["total"] == 1
            assert body["items"][0]["name"] == "base"
            assert any(
                u == "https://pve01:8006/api2/json/nodes/pve01/qemu/200/snapshot"
                for (_, u, *_) in _FakeProxmox.calls)
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_list_snapshots_pve_error_maps_502(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)

    class _ErrProxmox(_FakeProxmox):
        async def get(self, url, headers=None, params=None):
            _FakeProxmox.calls.append(("GET", url, params))
            return _FakeResp(500, "boom")

    monkeypatch.setattr(httpx, "AsyncClient", _ErrProxmox)
    _FakeProxmox.calls = []
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            hid = await _create_host(c)
            r = await c.get(f"/v1/proxmox/hosts/{hid}/vms/200/snapshots")
            assert r.status_code == 502
            assert r.json()["code"] == "PROXMOX_ERROR"
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_create_snapshot_returns_upid(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeProxmox)
    _FakeProxmox.calls = []
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            hid = await _create_host(c)
            r = await c.post(
                f"/v1/proxmox/hosts/{hid}/vms/200/snapshots",
                json={"snapname": "s1", "description": "d", "vmstate": True},
            )
            assert r.status_code == 200, r.text
            assert r.json()["upid"].startswith("UPID")
            posts = [(u, d) for (m, u, d) in _FakeProxmox.calls if m == "POST"]
            assert posts, "expected a POST to Proxmox"
            url, data = posts[0]
            assert url == "https://pve01:8006/api2/json/nodes/pve01/qemu/200/snapshot"
            assert data["snapname"] == "s1"
            assert data["vmstate"] == 1
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_delete_snapshot_returns_upid(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeProxmox)
    _FakeProxmox.calls = []
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            hid = await _create_host(c)
            r = await c.delete(f"/v1/proxmox/hosts/{hid}/vms/200/snapshots/s1")
            assert r.status_code == 200, r.text
            assert r.json()["upid"].startswith("UPID")
            deletes = [u for (m, u, *_) in _FakeProxmox.calls if m == "DELETE"]
            assert deletes == [
                "https://pve01:8006/api2/json/nodes/pve01/qemu/200/snapshot/s1"
            ]
    finally:
        await dbmod.dispose_engine()
