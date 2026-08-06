"""Secrets must never be echoed back by the API (#100).

`token_ref` holds the live Proxmox API token and Git PATs. Any caller able to
reach the backend could otherwise harvest every registered credential with a
single GET — amplified by the subnet-wide CORS default (#101).

These tests assert on the serialized response bodies, not on the models, so
they keep holding if someone re-adds the field through a different route.
"""
import json

import pytest
from httpx import ASGITransport, AsyncClient

TOKEN = "root@pam!deployer=1234abcd-dead-beef-0000-feedfacecafe"
PAT = "ghp_averysecretpersonalaccesstoken00000000"


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
    return create_app()


def _assert_no_secret(payload):
    blob = json.dumps(payload)
    for secret in (TOKEN, PAT):
        assert secret not in blob, f"response leaked a secret: {payload}"
    return blob


@pytest.mark.asyncio
async def test_proxmox_host_responses_never_include_token(tmp_path, monkeypatch):
    app = await _boot(tmp_path, monkeypatch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        created = await c.post("/v1/proxmox/hosts", json={
            "name": "pve01",
            "api_url": "https://10.0.0.5:8006",
            "node_name": "pve01",
            "token_ref": TOKEN,
            "token_scope": "PVEVMAdmin",
        })
        assert created.status_code == 201, created.text
        body = created.json()
        assert "token_ref" not in _assert_no_secret(body)
        assert body["has_token"] is True

        listed = await c.get("/v1/proxmox/hosts")
        assert listed.status_code == 200
        assert "token_ref" not in _assert_no_secret(listed.json())
        assert listed.json()["items"][0]["has_token"] is True


@pytest.mark.asyncio
async def test_catalog_source_responses_never_include_token(tmp_path, monkeypatch):
    """The Git PAT half of #100 — kept alongside so both are covered here."""
    app = await _boot(tmp_path, monkeypatch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        created = await c.post("/v1/catalog/sources", json={
            "provider": "github",
            "base_url": "https://github.com",
            "auth_kind": "pat",
            "token_ref": PAT,
        })
        assert created.status_code in (200, 201), created.text
        assert "token_ref" not in _assert_no_secret(created.json())

        listed = await c.get("/v1/catalog/sources")
        assert listed.status_code == 200
        assert "token_ref" not in _assert_no_secret(listed.json())


@pytest.mark.asyncio
async def test_openapi_response_schemas_expose_no_token_ref(tmp_path, monkeypatch):
    """No *response* schema may declare token_ref; request bodies still may."""
    app = await _boot(tmp_path, monkeypatch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        spec = (await c.get("/docs/openapi.json")).json()

    schemas = spec["components"]["schemas"]

    def _leaks(schema: dict) -> str:
        """Return the offending component name if this schema exposes a token."""
        ref = schema.get("$ref") or (schema.get("items") or {}).get("$ref", "")
        name = ref.rsplit("/", 1)[-1] if ref else ""
        if not name:
            return ""
        props = schemas.get(name, {}).get("properties", {})
        if "token_ref" in props:
            return name
        # Page[T] wrappers nest the payload under items
        nested = (props.get("items") or {}).get("items", {})
        return _leaks(nested) if nested else ""

    offenders = []
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            if not isinstance(op, dict):
                continue
            for code, resp in (op.get("responses") or {}).items():
                schema = ((resp.get("content") or {})
                          .get("application/json", {})
                          .get("schema", {}))
                bad = _leaks(schema)
                if bad:
                    offenders.append(f"{method.upper()} {path} -> {code} ({bad})")

    assert not offenders, f"response schemas expose token_ref: {offenders}"
