"""Registered Git sources are advisory metadata, not connectivity checks."""
from types import SimpleNamespace

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from tests.routes.test_proxmox_storage import _boot

API_TOKEN = "readiness-test-bearer-" + "x" * 32
SOURCE_TOKEN = "readiness-private-source-token"
PVE_TOKEN = "root@pam!readiness=private-pve-token"


async def secured_backend(tmp_path, monkeypatch):
    from app import main
    from app.core.config import Settings
    from app.routes.v1 import health

    monkeypatch.setenv("RANGE42_AUTH_MODE", "required")
    monkeypatch.setenv("RANGE42_API_TOKEN", API_TOKEN)
    _, dbmod = await _boot(tmp_path, monkeypatch)
    settings = Settings()
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(health, "settings", settings)
    return main.create_app(), dbmod


async def add_source(dbmod):
    from app.core.models import Source

    async with dbmod.get_session_factory()() as session:
        session.add(Source(id="private-source", provider="gitlab", base_url="https://private.example", auth_kind="pat", token_ref=SOURCE_TOKEN))
        await session.commit()


def refuse_network(monkeypatch):
    def upstream(request):
        pytest.fail("Registration-only readiness must not probe a Git provider")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: AsyncClient(transport=httpx.MockTransport(upstream)))


@pytest.mark.asyncio
@pytest.mark.parametrize("registered", [0, 1])
async def test_git_registration_is_explicitly_unchecked_and_does_not_block_readiness(tmp_path, monkeypatch, registered):
    app, dbmod = await secured_backend(tmp_path, monkeypatch)
    try:
        if registered:
            await add_source(dbmod)
        refuse_network(monkeypatch)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            response = await client.get("/v1/health/ready", headers={"Authorization": "Bearer " + API_TOKEN})
        assert response.status_code == 200
        assert response.json()["ready"] is True
        assert response.json()["checks"]["git"] == {
            "ok": None, "required": False, "connectivity": "not_checked", "sources_registered": registered,
        }
        assert SOURCE_TOKEN not in response.text
        assert "private.example" not in response.text
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_git_registration_does_not_load_or_decrypt_source_credentials(tmp_path, monkeypatch):
    app, dbmod = await secured_backend(tmp_path, monkeypatch)
    try:
        await add_source(dbmod)
        async with dbmod.get_session_factory()() as session:
            stored = (await session.execute(text("SELECT token_ref FROM sources"))).scalar_one()
        assert stored != SOURCE_TOKEN

        def unexpected_decryption(value):
            pytest.fail("Counting registered sources must not load credential fields")

        monkeypatch.setattr("app.core.credential_store.decrypt_credential", unexpected_decryption)
        refuse_network(monkeypatch)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            response = await client.get("/v1/health/ready", headers={"Authorization": "Bearer " + API_TOKEN})
        assert response.status_code == 200
        assert response.json()["checks"]["git"]["sources_registered"] == 1
        assert SOURCE_TOKEN not in response.text and stored not in response.text
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
@pytest.mark.parametrize("upstream_status", [200, 401, 503, "unreachable"])
async def test_git_advisory_preserves_authenticated_proxmox_success_and_failure(tmp_path, monkeypatch, upstream_status):
    from app.core.models import ProxmoxHost

    app, dbmod = await secured_backend(tmp_path, monkeypatch)
    try:
        await add_source(dbmod)
        async with dbmod.get_session_factory()() as session:
            session.add(ProxmoxHost(id="pve", name="pve01", api_url="https://pve01:8006", node_name="pve01", token_ref=PVE_TOKEN))
            await session.commit()
            stored = (await session.execute(text("SELECT token_ref FROM proxmox_hosts"))).scalar_one()
        assert stored != PVE_TOKEN
        calls = []

        def upstream(request):
            calls.append(str(request.url))
            assert request.headers["Authorization"] == "PVEAPIToken=" + PVE_TOKEN
            if upstream_status == "unreachable":
                raise httpx.ConnectError("private diagnostic", request=request)
            return httpx.Response(upstream_status, json={"data": {"version": "test"}})

        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: AsyncClient(transport=httpx.MockTransport(upstream)))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            response = await client.get("/v1/health/ready", headers={"Authorization": "Bearer " + API_TOKEN})
        body = response.json()
        assert response.status_code == 200
        assert calls == ["https://pve01:8006/api2/json/version"]
        assert body["ready"] is (upstream_status == 200)
        assert body["checks"]["proxmox"] == {"ok": upstream_status == 200, "hosts": [{"id": "pve", "ok": upstream_status == 200, "status": upstream_status}]}
        assert body["checks"]["git"].get("required") is False
        assert all(secret not in response.text for secret in (PVE_TOKEN, SOURCE_TOKEN, stored, "private diagnostic"))
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_workspace_failure_still_blocks_with_unchecked_git(tmp_path, monkeypatch):
    from app.routes.v1 import health

    app, dbmod = await secured_backend(tmp_path, monkeypatch)
    try:
        blocked = tmp_path / "not-a-directory"
        blocked.write_text("existing")
        monkeypatch.setattr(health, "settings", SimpleNamespace(workspace_root=blocked))
        refuse_network(monkeypatch)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            response = await client.get("/v1/health/ready", headers={"Authorization": "Bearer " + API_TOKEN})
        assert response.json()["ready"] is False
        assert response.json()["checks"]["workspace_writable"]["ok"] is False
        assert response.json()["checks"]["git"].get("ok") is None
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}])
async def test_readiness_still_requires_bearer_auth(tmp_path, monkeypatch, headers):
    app, dbmod = await secured_backend(tmp_path, monkeypatch)
    try:
        refuse_network(monkeypatch)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            response = await client.get("/v1/health/ready", headers=headers)
        assert response.status_code == 401
        assert "checks" not in response.json()
        assert API_TOKEN not in response.text
    finally:
        await dbmod.dispose_engine()
