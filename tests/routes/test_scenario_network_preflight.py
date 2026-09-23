"""Network declarations are checked for the pinned scenario and requested scope."""
import json

import pytest
from httpx import ASGITransport, AsyncClient

from tests.routes.test_project_scenario_execution import _boot, seed_scenario, healthy_host


@pytest.mark.asyncio
async def test_preflight_reports_missing_sdn_bundle_from_pinned_network_manifest(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        await seed_scenario(dbmod, tmp_path, extra_files={"manifest/scenario_networks.json": json.dumps({
            "mode": "sdn", "zone": "r42demo", "vnets": [{"vnet": "r42net", "subnet": "10.42.70.0/24", "snat": False}],
        })})
        monkeypatch.delenv("RANGE42_BUNDLE_DIR", raising=False)
        healthy_host(monkeypatch)
        async with AsyncClient(transport=ASGITransport(app), base_url="http://t") as client:
            response = await client.post("/v1/deployments/dep-1/preflight")
        assert response.status_code == 200
        report = response.json()
        assert report["result"] == "block"
        assert any(check["code"] == "SDN_BUNDLE_UNAVAILABLE" for check in report["checks"])
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_preflight_validates_requested_configuration_entrypoint(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        await seed_scenario(dbmod, tmp_path, missing="configure.yml")
        healthy_host(monkeypatch)
        async with AsyncClient(transport=ASGITransport(app), base_url="http://t") as client:
            response = await client.post("/v1/deployments/dep-1/preflight", json={"scope": "configure"})
        assert response.status_code == 200
        report = response.json()
        assert report["result"] == "block"
        assert any("configure.yml" in check["detail"] for check in report["checks"])
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_preflight_rejects_unknown_scope(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app), base_url="http://t") as client:
            response = await client.post("/v1/deployments/dep-1/preflight", json={"scope": "pretend"})
        assert response.status_code == 422
    finally:
        await dbmod.dispose_engine()
