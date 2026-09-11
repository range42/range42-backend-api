"""A maintenance writer must drain real handlers, including cancelled sync work."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import fcntl
import importlib
import importlib.util
import os
import subprocess
import sys
import threading

from fastapi import BackgroundTasks, FastAPI
import httpx
import pytest


def gate_module():
    assert importlib.util.find_spec("app.core.maintenance") is not None, "finite HTTP handlers need a maintenance admission gate"
    return importlib.import_module("app.core.maintenance")


@contextmanager
def exclusive(path):
    descriptor = os.open(path, os.O_RDWR)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def guarded_app(tmp_path):
    module = gate_module()
    gate = module.MaintenanceGate(tmp_path / "maintenance.lock")
    app = FastAPI()
    app.add_middleware(module.MaintenanceMiddleware, gate=gate)
    return app, gate


@pytest.mark.parametrize("method,path,headers", [
    ("POST", "/v0/admin/run/bundles/test/run", {}),
    ("POST", "/v1/proxmox/hosts/host/vms/1/start", {}),
    ("POST", "/v1/deployments/deployment/events", {"Accept": "text/event-stream"}),
    ("HEAD", "/v1/health", {}),
    ("OPTIONS", "/v1/health", {}),
    ("GET", "/v1/deployments/d/events/download", {}),
    ("GET", "/v1/deployments/d/events/", {}),
])
async def test_exclusive_maintenance_blocks_every_finite_handler(tmp_path, method, path, headers):
    app, gate = guarded_app(tmp_path)
    called = []
    app.add_api_route(path, lambda: called.append(True), methods=[method])
    gate.capability()
    with exclusive(gate.path):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.request(method, path, headers=headers)
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert called == []


@pytest.mark.parametrize("path", ["/v1/health", "/v1/deployments/abc123/events"])
async def test_only_exact_health_and_sse_gets_bypass_maintenance(tmp_path, path):
    app, gate = guarded_app(tmp_path)
    app.get(path)(lambda: {"ok": True})
    gate.capability()
    with exclusive(gate.path):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get(path)).status_code == 200


@pytest.mark.parametrize("as_background", [False, True])
async def test_cancelled_http_request_keeps_gate_until_real_sync_work_finishes(tmp_path, as_background):
    app, gate = guarded_app(tmp_path)
    started, finish, completed = threading.Event(), threading.Event(), threading.Event()

    def work():
        started.set()
        assert finish.wait(timeout=5)
        completed.set()

    if as_background:
        @app.post("/v0/legacy")
        def execute(background: BackgroundTasks):
            background.add_task(work)
            return {"accepted": True}
    else:
        app.post("/v0/legacy")(work)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        request = asyncio.create_task(client.post("/v0/legacy"))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            request.cancel()
            await asyncio.sleep(0)
            request.cancel()  # Repeated caller cancellation must not abandon work.
            await asyncio.sleep(0)
            with pytest.raises(BlockingIOError):
                with exclusive(gate.path):
                    pass
            assert not completed.is_set()
        finally:
            finish.set()
            await asyncio.gather(request, return_exceptions=True)
            assert await asyncio.to_thread(completed.wait, 2)
            await gate.drain()
        with exclusive(gate.path):
            pass


async def test_downstream_error_releases_gate(tmp_path):
    app, gate = guarded_app(tmp_path)
    @app.post("/fail")
    async def fail():
        raise RuntimeError("expected handler failure")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        with pytest.raises(RuntimeError, match="expected"):
            await client.post("/fail")
    with exclusive(gate.path):
        pass


def test_restarted_process_obeys_same_persistent_exclusive_lock(tmp_path):
    gate = gate_module().MaintenanceGate(tmp_path / "maintenance.lock")
    first = gate.capability()
    script = """import json, sys
from pathlib import Path
from app.core.maintenance import MaintenanceGate
gate = MaintenanceGate(Path(sys.argv[1]))
assert gate.acquire_shared() is None
print(json.dumps(gate.capability()))
"""
    with exclusive(gate.path):
        result = subprocess.run([sys.executable, "-c", script, str(gate.path)],
                                capture_output=True, text=True, check=True)
    import json
    second = json.loads(result.stdout)
    assert second["process"]["pid"] != first["process"]["pid"]
    assert second["lock"] == first["lock"]
    assert first["protocol"] == "flock-http-v1"


def test_replaced_lock_path_cannot_open_an_unlocked_gate(tmp_path):
    gate = gate_module().MaintenanceGate(tmp_path / "maintenance.lock")
    gate.capability()
    original = tmp_path / "original.lock"
    gate.path.rename(original)
    gate.path.touch(mode=0o600)
    with exclusive(original), pytest.raises(ValueError, match="changed"):
        gate.acquire_shared()


@pytest.mark.parametrize("kind", ["symlink", "public"])
def test_unsafe_lock_file_is_rejected(tmp_path, kind):
    target = tmp_path / "maintenance.lock"
    if kind == "symlink":
        unrelated = tmp_path / "unrelated"
        unrelated.write_text("protected")
        target.symlink_to(unrelated)
    else:
        target.touch(mode=0o666)
        target.chmod(0o666)
    with pytest.raises(ValueError, match="private|symbolic"):
        gate_module().MaintenanceGate(target).capability()


def test_real_api_capability_requires_auth_and_reports_exact_process_and_lock(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app import main
    from app.core.config import Settings
    token = "maintenance-test-bearer-token-123456789"
    lock = tmp_path / "maintenance.lock"
    monkeypatch.setenv("RANGE42_AUTH_MODE", "required")
    monkeypatch.setenv("RANGE42_API_TOKEN", token)
    monkeypatch.setenv("RANGE42_MAINTENANCE_LOCK_FILE", str(lock))
    monkeypatch.setattr(main, "settings", Settings())
    client = TestClient(main.create_app())
    path = "/v1/admin/maintenance"
    assert client.get(path).status_code == 401
    response = client.get(path, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    proof = response.json()
    assert proof["protocol"] == "flock-http-v1" and proof["enabled"] is True
    assert proof["process"]["pid"] == os.getpid()
    assert proof["lock"] == {"path": str(lock), "device": lock.stat().st_dev,
                             "inode": lock.stat().st_ino, "uid": os.getuid()}
    with exclusive(lock):
        assert client.post("/v0/admin/run/bundles/test/run", headers={"Authorization": f"Bearer {token}"}).status_code == 503
        assert client.post("/v0/admin/run/bundles/test/run").status_code == 401
        assert client.get("/v1/health").status_code == 200


def test_unconfigured_api_does_not_claim_a_maintenance_gate(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    from app.core.config import Settings
    monkeypatch.delenv("RANGE42_MAINTENANCE_LOCK_FILE", raising=False)
    monkeypatch.setattr(main, "settings", Settings())
    response = TestClient(main.create_app()).get("/v1/admin/maintenance")
    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert response.json()["lock"] is None
