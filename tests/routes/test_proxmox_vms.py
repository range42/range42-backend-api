"""/v1/proxmox/hosts/{id}/vms — list + lifecycle (direct Proxmox API via host token)."""
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
    """Stands in for httpx.AsyncClient; captures calls + returns canned PVE data.

    Note: the test driver imports AsyncClient by name (bound before patching),
    so only the route module's attribute-access `httpx.AsyncClient` is replaced.
    """

    calls: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None):
        _FakeProxmox.calls.append(("GET", url, None))
        if url.endswith("/qemu"):
            return _FakeResp(200, [
                {"vmid": 4001, "name": "vuln-box-01", "status": "running",
                 "maxmem": 4294967296, "cpus": 1, "uptime": 100, "template": 0,
                 "tags": "admin"},
                {"vmid": 9000, "name": "ubuntu-template", "status": "stopped",
                 "template": 1},
            ])
        if url.endswith("/lxc"):
            return _FakeResp(200, [
                {"vmid": 200, "name": "ct-1", "status": "stopped"},
            ])
        if "/tasks/" in url and url.endswith("/status"):
            return _FakeResp(200, {
                "upid": "UPID:pve01:0001:delete::",
                "status": "stopped",
                "exitstatus": "OK",
                "pid": 1,
            })
        return _FakeResp(404, [])

    async def post(self, url, headers=None, json=None):
        _FakeProxmox.calls.append(("POST", url))
        return _FakeResp(200, "UPID:pve01:0000:start::")

    async def delete(self, url, headers=None, params=None):
        _FakeProxmox.calls.append(("DELETE", url, params))
        return _FakeResp(200, "UPID:pve01:0001:delete::")


async def _create_host(c):
    r = await c.post("/v1/proxmox/hosts", json={
        "name": "pve01", "api_url": "https://pve01:8006/", "node_name": "pve01",
        "token_ref": "root@pam!tok=secret", "default_bridge": "vmbr0",
    })
    return r.json()["id"]


@pytest.mark.asyncio
async def test_list_host_vms_merges_qemu_and_lxc(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeProxmox)
    _FakeProxmox.calls = []
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            hid = await _create_host(c)
            r = await c.get(f"/v1/proxmox/hosts/{hid}/vms")
            assert r.status_code == 200, r.text
            byid = {v["vmid"]: v for v in r.json()["items"]}
            assert byid[4001]["type"] == "qemu"
            assert byid[4001]["status"] == "running"
            assert byid[4001]["template"] is False
            assert byid[4001]["node"] == "pve01"
            assert byid[9000]["template"] is True
            assert byid[200]["type"] == "lxc"
            # trailing slash in api_url must not produce a double slash
            assert any(u == "https://pve01:8006/api2/json/nodes/pve01/qemu"
                       for (_, u, *_) in _FakeProxmox.calls)
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_list_host_vms_unknown_host_404(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/v1/proxmox/hosts/nope/vms")
            assert r.status_code == 404
            assert r.json()["code"] == "NOT_FOUND"
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_vm_action_start_posts_to_pve_status_endpoint(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeProxmox)
    _FakeProxmox.calls = []
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            hid = await _create_host(c)
            r = await c.post(
                f"/v1/proxmox/hosts/{hid}/vms/4001/status/start?vmtype=qemu"
            )
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "accepted"
            assert r.json()["upid"].startswith("UPID")
            posts = [u for (m, u, *_) in _FakeProxmox.calls if m == "POST"]
            assert posts == [
                "https://pve01:8006/api2/json/nodes/pve01/qemu/4001/status/start"
            ]
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_vm_action_rejects_unknown_action(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            hid = await _create_host(c)
            r = await c.post(f"/v1/proxmox/hosts/{hid}/vms/4001/status/destroy")
            assert r.status_code == 400
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_vm_action_guards_protected_vmids_on_destructive(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeProxmox)
    _FakeProxmox.calls = []
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            hid = await _create_host(c)
            # 100 (pmg01) / 101 (zbx01) are protected — destructive actions refused
            r = await c.post(f"/v1/proxmox/hosts/{hid}/vms/100/status/stop")
            assert r.status_code == 409
            assert r.json()["code"] == "VMID_PROTECTED"
            # the canonical guard also protects e.g. the 9000-9999 template range
            r = await c.post(f"/v1/proxmox/hosts/{hid}/vms/9000/status/shutdown")
            assert r.status_code == 409
            # no destructive POST reached Proxmox
            assert [m for (m, *_) in _FakeProxmox.calls if m == "POST"] == []
            # non-destructive start IS allowed on a protected vmid
            r = await c.post(f"/v1/proxmox/hosts/{hid}/vms/101/status/start")
            assert r.status_code == 200, r.text
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_vm_delete_happy_path(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeProxmox)
    _FakeProxmox.calls = []
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            hid = await _create_host(c)
            r = await c.delete(f"/v1/proxmox/hosts/{hid}/vms/2001?vmtype=qemu&purge=true")
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "accepted"
            assert r.json()["upid"].startswith("UPID")
            deletes = [(u, p) for (m, u, p) in _FakeProxmox.calls if m == "DELETE"]
            assert deletes, "expected a DELETE to Proxmox"
            url, params = deletes[0]
            assert url == "https://pve01:8006/api2/json/nodes/pve01/qemu/2001"
            assert params.get("purge") == 1
            assert params.get("destroy-unreferenced-disks") == 1
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_vm_delete_refuses_protected_vmid(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeProxmox)
    _FakeProxmox.calls = []
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            hid = await _create_host(c)
            r = await c.delete(f"/v1/proxmox/hosts/{hid}/vms/100?vmtype=qemu")
            assert r.status_code == 409
            assert r.json()["code"] == "VMID_PROTECTED"
            assert [m for (m, *_) in _FakeProxmox.calls if m == "DELETE"] == []
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_vm_delete_running_guest_conflict(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)

    class _RunningProxmox(_FakeProxmox):
        async def delete(self, url, headers=None, params=None):
            _FakeProxmox.calls.append(("DELETE", url, params))
            return _FakeResp(500, "can't remove VM 4001 - running, stop it first")

    monkeypatch.setattr(httpx, "AsyncClient", _RunningProxmox)
    _FakeProxmox.calls = []
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            hid = await _create_host(c)
            r = await c.delete(f"/v1/proxmox/hosts/{hid}/vms/2001?vmtype=qemu")
            assert r.status_code == 409, r.text
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_task_status_stopped_ok(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeProxmox)
    _FakeProxmox.calls = []
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            hid = await _create_host(c)
            upid = "UPID:pve01:0001:delete::"
            r = await c.get(f"/v1/proxmox/hosts/{hid}/tasks/{upid}/status")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["status"] == "stopped"
            assert body["exitstatus"] == "OK"
            assert body["node"] == "pve01"
            gets = [u for (m, u, *_) in _FakeProxmox.calls if m == "GET"]
            assert any("/tasks/UPID%3Apve01%3A0001%3Adelete%3A%3A/status" in u for u in gets)
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_task_status_stopped_error(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)

    class _FailedTaskProxmox(_FakeProxmox):
        async def get(self, url, headers=None):
            _FakeProxmox.calls.append(("GET", url, None))
            if "/tasks/" in url and url.endswith("/status"):
                return _FakeResp(200, {
                    "upid": "UPID:pve01:0001:delete::",
                    "status": "stopped",
                    "exitstatus": "command failed",
                    "pid": 1,
                })
            return _FakeResp(404, [])

    monkeypatch.setattr(httpx, "AsyncClient", _FailedTaskProxmox)
    _FakeProxmox.calls = []
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            hid = await _create_host(c)
            r = await c.get(f"/v1/proxmox/hosts/{hid}/tasks/UPID:pve01:0001:delete::/status")
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "stopped"
            assert r.json()["exitstatus"] == "command failed"
    finally:
        await dbmod.dispose_engine()
