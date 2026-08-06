"""The spec must describe what v1 actually returns (#116).

The committed openapi.json bootstraps the Kong gateway and is what clients
would be generated from, so a wrong declaration is not cosmetic: it produces
clients that mis-parse real responses.
"""
import json

import pytest
from httpx import ASGITransport, AsyncClient

ENVELOPE_KEYS = {"error", "message", "code", "details", "trace_id", "timestamp"}


async def _boot(tmp_path, monkeypatch):
    monkeypatch.setenv("RANGE42_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    monkeypatch.setenv("RANGE42_WORKSPACE_ROOT", str(tmp_path))
    from importlib import reload

    from app.core import config as cfg
    reload(cfg)
    import app.core.db as dbmod
    reload(dbmod)
    from app.core.models import Base

    async with dbmod.get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from app.main import create_app
    return create_app()


@pytest.mark.asyncio
async def test_a_real_422_matches_the_declared_schema(tmp_path, monkeypatch):
    """Anchor: the runtime body and the declaration must agree.

    Asserting only on the spec would let both drift together.
    """
    app = await _boot(tmp_path, monkeypatch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.post("/v1/projects/", json={})  # missing required fields
        spec = (await c.get("/docs/openapi.json")).json()

    assert r.status_code == 422
    assert set(r.json()) == ENVELOPE_KEYS, r.text

    declared = (spec["paths"]["/v1/projects/"]["post"]["responses"]["422"]
                ["content"]["application/json"]["schema"]["$ref"])
    name = declared.rsplit("/", 1)[-1]
    props = set(spec["components"]["schemas"][name]["properties"])
    assert props == ENVELOPE_KEYS, f"{name} does not describe the real body"


@pytest.mark.asyncio
async def test_no_v1_operation_still_declares_the_fastapi_default(
    tmp_path, monkeypatch,
):
    app = await _boot(tmp_path, monkeypatch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        spec = (await c.get("/docs/openapi.json")).json()

    offenders = []
    for path, ops in spec["paths"].items():
        if not path.startswith("/v1/"):
            continue
        for method, op in ops.items():
            if not isinstance(op, dict):
                continue
            resp = (op.get("responses") or {}).get("422")
            if not resp:
                continue
            ref = json.dumps(resp)
            if "HTTPValidationError" in ref:
                offenders.append(f"{method.upper()} {path}")
    assert not offenders, f"still advertising FastAPI's default 422: {offenders}"


@pytest.mark.asyncio
async def test_events_stream_is_declared_as_sse(tmp_path, monkeypatch):
    """It returns EventSourceResponse; advertising JSON makes consumers await
    a body that never completes instead of opening a stream."""
    app = await _boot(tmp_path, monkeypatch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        spec = (await c.get("/docs/openapi.json")).json()

    content = (spec["paths"]["/v1/deployments/{deployment_id}/events"]["get"]
               ["responses"]["200"]["content"])
    assert "text/event-stream" in content
    assert "application/json" not in content
