"""/v1/proxmox/hosts/{id}/storage — pools, content, download-url (direct PVE API)."""
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
        if url.endswith("/storage"):
            return _FakeResp(200, [
                {"storage": "local", "type": "dir", "content": "iso,vztmpl",
                 "total": 100, "used": 40, "avail": 60, "active": 1},
                {"storage": "local-zfs", "type": "zfspool", "content": "images"},
            ])
        if url.endswith("/content"):
            return _FakeResp(200, [
                {"volid": "local:iso/ubuntu-22.04.iso", "content": "iso",
                 "size": 1234, "format": "iso"},
            ])
        return _FakeResp(404, [])

    async def post(self, url, headers=None, data=None):
        _FakeProxmox.calls.append(("POST", url, data))
        return _FakeResp(200, "UPID:pve01:0000:download::")


async def _create_host(c):
    r = await c.post("/v1/proxmox/hosts", json={
        "name": "pve01", "api_url": "https://pve01:8006/", "node_name": "pve01",
        "token_ref": "root@pam!tok=secret", "default_bridge": "vmbr0",
    })
    return r.json()["id"]


@pytest.mark.asyncio
async def test_list_storage_pools(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeProxmox)
    _FakeProxmox.calls = []
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            hid = await _create_host(c)
            r = await c.get(f"/v1/proxmox/hosts/{hid}/storage")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["total"] == 2
            assert body["items"][0] == {
                "storage": "local", "type": "dir", "content": "iso,vztmpl",
                "total": 100, "used": 40, "avail": 60, "active": True,
            }
            assert body["items"][1]["active"] is None
            assert any(u == "https://pve01:8006/api2/json/nodes/pve01/storage"
                       for (_, u, *_) in _FakeProxmox.calls)
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_list_storage_unknown_host_404(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/v1/proxmox/hosts/nope/storage")
            assert r.status_code == 404
            assert r.json()["code"] == "NOT_FOUND"
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_list_storage_content_iso_derives_name(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeProxmox)
    _FakeProxmox.calls = []
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            hid = await _create_host(c)
            r = await c.get(
                f"/v1/proxmox/hosts/{hid}/storage/local/content?content=iso"
            )
            assert r.status_code == 200, r.text
            item = r.json()["items"][0]
            assert item["name"] == "ubuntu-22.04.iso"
            assert item["volid"] == "local:iso/ubuntu-22.04.iso"
            assert item["size"] == 1234
            # content type forwarded to PVE as a query param
            assert any(p == {"content": "iso"} for (_, _u, p) in _FakeProxmox.calls
                       if p is not None)
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_list_storage_content_invalid_type_422(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            hid = await _create_host(c)
            r = await c.get(
                f"/v1/proxmox/hosts/{hid}/storage/local/content?content=bogus"
            )
            assert r.status_code == 422
    finally:
        await dbmod.dispose_engine()
