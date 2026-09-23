"""Readiness requires a successful authenticated Proxmox response."""
from dataclasses import replace

import httpx
import pytest

from app.core.db import build_engine, session_factory
from app.core.models import Base, ProxmoxHost
from app.routes.v1 import health


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 204, 302, 400, 401, 403, 404, 429, 500, 503])
async def test_readiness_requires_success_from_every_proxmox_host(tmp_path, monkeypatch, status):
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    factory = session_factory(engine)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(health, "settings", replace(health.settings, workspace_root=tmp_path))
    original_client = httpx.AsyncClient

    def respond(request):
        assert request.headers["Authorization"] == "PVEAPIToken=test@pve!ready=fake-secret"
        return httpx.Response(status if request.url.host == "second.test" else 200)

    monkeypatch.setattr(health.httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(respond), **kwargs,
    ))
    try:
        async with factory() as session:
            for name in ("first", "second"):
                session.add(ProxmoxHost(
                    id=name, name=name, api_url=f"https://{name}.test:8006", node_name="pve",
                    token_ref="test@pve!ready=fake-secret",
                ))
            await session.commit()
            result = await health.readiness(session)
        expected = 200 <= status < 300
        hosts = {host["id"]: host for host in result["checks"]["proxmox"]["hosts"]}
        assert hosts["first"]["ok"] is True
        assert hosts["second"] == {"id": "second", "status": status, "ok": expected}
        assert result["checks"]["proxmox"]["ok"] is expected
        assert result["ready"] is expected
    finally:
        await engine.dispose()
