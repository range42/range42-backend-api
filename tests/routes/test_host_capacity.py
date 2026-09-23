"""Authenticated capacity API preserves safe partial results."""
import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from tests.routes.test_proxmox_storage import _boot, _create_host
from tests.core.test_host_capacity import node_status, storage


@pytest.mark.asyncio
async def test_host_capacity_endpoint_reports_read_only_measurements(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)

    def upstream(request):
        assert request.method == "GET"
        return httpx.Response(200, json={"data": node_status() if request.url.path.endswith("/status") else [storage()]})

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            host_id = await _create_host(client)
            monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: AsyncClient(transport=httpx.MockTransport(upstream)))
            response = await client.get(f"/v1/proxmox/hosts/{host_id}/capacity")
            assert response.status_code == 200, response.text
            assert response.json()["host_id"] == host_id
            assert response.json()["cpu"]["logical_cpus"] == 8
            assert "secret" not in response.text
            assert (await client.get("/v1/proxmox/hosts/missing/capacity")).status_code == 404
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_capacity_requires_configured_bearer_token(tmp_path, monkeypatch):
    monkeypatch.setenv("RANGE42_AUTH_MODE", "required")
    monkeypatch.setenv("RANGE42_API_TOKEN", "capacity-test-token-" + "a" * 32)
    _, dbmod = await _boot(tmp_path, monkeypatch)
    from app import main
    from app.core.config import settings
    monkeypatch.setattr(main, "settings", settings)
    app = main.create_app()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            response = await client.get("/v1/proxmox/hosts/any/capacity")
            assert response.status_code == 401
    finally:
        await dbmod.dispose_engine()
