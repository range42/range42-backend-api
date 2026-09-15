"""Deployment requests cannot silently accept an unused target credential."""

import stat

import pytest
import yaml
from ansible.parsing.vault import VaultLib, VaultSecret
from httpx import ASGITransport, AsyncClient

from tests.routes.test_deployment_vault_seed import _boot, _payload


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["", "unused-token-secret", "user@pve!old=unused-token-secret"])
async def test_obsolete_token_is_rejected_before_deployment_or_workspace_creation(
    tmp_path, monkeypatch, capsys, token,
):
    app = await _boot(tmp_path, monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/deployments/", json=_payload(
            secrets={"proxmox_token": token, "vault_password": "unused-password"},
        ))
        assert response.status_code == 422
        assert response.json()["code"] == "DEPLOYMENT_TOKEN_UNSUPPORTED"
        assert "secrets.proxmox_token" in response.text
        assert "unused-token-secret" not in response.text
        assert "unused-password" not in response.text
        assert (await client.get("/v1/deployments/")).json()["items"] == []
    assert not (tmp_path / "ws/ALPHA-demo_lab").exists()
    logs = capsys.readouterr().out
    assert "unused-token-secret" not in logs
    assert "unused-password" not in logs


@pytest.mark.asyncio
async def test_rejected_token_preserves_vault_and_retry_uses_registered_target(tmp_path, monkeypatch):
    app = await _boot(tmp_path, monkeypatch)
    from app.core.db import get_session_factory
    from app.core.models import ProxmoxHost
    from app.core.scenario_runtime import target_runtime_variables

    workspace = tmp_path / "ws/ALPHA-demo_lab"
    secrets_dir = workspace / "secrets"
    secrets_dir.mkdir(parents=True, mode=0o700)
    password = secrets_dir / "vault_pass.txt"
    password.write_text("operator-vault-password\n")
    password.chmod(0o600)
    vault = VaultLib([("default", VaultSecret(password.read_bytes().strip()))])
    original = {
        "default_admin_vm_ci_password": "operator-guest-password",
        "proxmox_api_host": "old.example:8006",
        "proxmox_api_user": "old@pve",
        "proxmox_api_token_id": "old",
        "proxmox_api_token_secret": "old-vault-token",
        "unrelated_content": {"value": "preserve-this"},
    }
    ciphertext = vault.encrypt(yaml.safe_dump(original).encode())
    vault_path = secrets_dir / "default_vault.yml"
    vault_path.write_bytes(ciphertext)
    vault_path.chmod(0o600)

    async with get_session_factory()() as session:
        host = await session.get(ProxmoxHost, "h1")
        host.api_url = "https://selected.example:8006/"
        host.node_name = "selected-node"
        host.token_ref = "selected@pve!deployment=registered-secret"
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        rejected = await client.post("/v1/deployments/", json=_payload(secrets={
            "proxmox_token": "discarded-secret", "vault_password": "wrong-vault-password",
        }))
        assert rejected.status_code == 422
        assert password.read_text() == "operator-vault-password\n"
        assert vault_path.read_bytes() == ciphertext
        assert (await client.get("/v1/deployments/")).json()["items"] == []
        retry = await client.post("/v1/deployments/", json=_payload())
        assert retry.status_code == 201
        assert retry.json()["target_host_id"] == "h1"
        assert "registered-secret" not in retry.text

    async with get_session_factory()() as session:
        selected = await session.get(ProxmoxHost, "h1")
        runtime = target_runtime_variables(selected, workspace)
    assert runtime["proxmox_api_host"] == "selected.example:8006"
    assert runtime["proxmox_node"] == "selected-node"
    assert runtime["proxmox_api_user"] == "selected@pve"
    assert runtime["proxmox_api_token_id"] == "deployment"
    assert runtime["proxmox_api_token_secret"] == "registered-secret"
    assert runtime["default_admin_vm_ci_password"] == "operator-guest-password"
    assert yaml.safe_load(vault.decrypt(vault_path.read_bytes())) == original
    assert vault_path.read_bytes() == ciphertext
    assert stat.S_IMODE(vault_path.stat().st_mode) == 0o600
    assert not (secrets_dir / "proxmox_token.yml").exists()
