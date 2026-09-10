"""Deployment creation wires operator defaults without replacing caller credentials."""

import pytest
from httpx import ASGITransport, AsyncClient

from tests.core.test_workspace_template import template
from tests.routes.test_deployment_vault_seed import _boot, _payload


@pytest.mark.asyncio
async def test_deployment_create_inherits_operator_credentials(
    tmp_path, monkeypatch, template
):
    app = await _boot(tmp_path, monkeypatch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/v1/deployments/", json=_payload())
    assert response.status_code == 201, response.text
    secrets = tmp_path / "ws/ALPHA-demo_lab/secrets"
    assert (secrets / "vault_pass.txt").read_text() == "private-test-password"
    assert "private-test-password" not in response.text
    assert "private-test-key" not in response.text


@pytest.mark.asyncio
async def test_explicit_password_does_not_inherit_a_different_vault(
    tmp_path, monkeypatch, template
):
    app = await _boot(tmp_path, monkeypatch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/deployments/",
            json=_payload(secrets={"vault_password": "caller-secret"}),
        )
    assert response.status_code == 201, response.text
    secrets = tmp_path / "ws/ALPHA-demo_lab/secrets"
    assert (secrets / "vault_pass.txt").read_text() == "caller-secret"
    assert not (secrets / "default_vault.yml").exists()


@pytest.mark.asyncio
async def test_invalid_operator_template_reports_its_own_error(tmp_path, monkeypatch):
    monkeypatch.setenv("RANGE42_WORKSPACE_TEMPLATE_DIR", str(tmp_path / "missing"))
    app = await _boot(tmp_path, monkeypatch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/v1/deployments/", json=_payload())
    assert response.status_code == 500
    assert response.json()["code"] == "WORKSPACE_TEMPLATE_INVALID"
    assert not (tmp_path / "ws/ALPHA-demo_lab").exists()
