"""Authoring support is explicit, installation-scoped and safe to read."""
import httpx
import pytest
from fastapi import FastAPI

from app.core.access import Principal, allowed
from app.core.errors import Range42Error
from app.routes.v1 import router


@pytest.mark.asyncio
@pytest.mark.parametrize("authorized", [False, True])
async def test_native_capabilities_report_template_inheritance_without_inventing_bootstrap_features(monkeypatch, authorized):
    monkeypatch.setenv("RANGE42_SCENARIO_MANAGEMENT_ACCESS", "1" if authorized else "0")
    from app.core import runtime_operations
    monkeypatch.setattr(runtime_operations, "operation_profile", lambda kind: {
        "contract": "native-sdn-20260921", "fingerprint": "a" * 64,
        "operations": ["vm_firewall", "scenario_firewall", "sdn_snat"], "dependencies": [],
    })
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/proxmox/runtime-capabilities")
    assert response.status_code == 200
    assert response.json() == {
        "version": 1, "available": True, "contract": "native-sdn-20260921", "fingerprint": "a" * 64,
        "operations": ["vm_firewall", "scenario_firewall", "sdn_snat"],
        "bootstrap_features": [], "reason": None,
        "management_access_available": authorized,
    }
    assert allowed(Principal("reader", "viewer"), "GET", "/v1/proxmox/runtime-capabilities")


@pytest.mark.asyncio
async def test_missing_runtime_reports_unknown_support_without_installation_paths(monkeypatch):
    from app.core import runtime_operations
    def unavailable(kind):
        raise Range42Error(status=409, code="RUNTIME_CAPABILITY_MISSING", error="conflict", message="private/path")
    monkeypatch.setattr(runtime_operations, "operation_profile", unavailable)
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/proxmox/runtime-capabilities")
    assert response.status_code == 200
    assert response.json()["available"] is False
    assert response.json()["bootstrap_features"] == []
    assert "private/path" not in response.text
