"""Tests for /v1/admin/retention GET + PUT."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_retention_default_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("RANGE42_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("RANGE42_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    from importlib import reload
    from app.core import config as cfg
    reload(cfg)
    from app import main as app_main
    reload(app_main)
    app = app_main.create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/v1/admin/retention")
        assert r.status_code == 200
        assert r.json() == {"keep_count": 5, "keep_days": 7}


@pytest.mark.asyncio
async def test_retention_put_persists_atomically(tmp_path, monkeypatch):
    monkeypatch.setenv("RANGE42_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("RANGE42_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    from importlib import reload
    from app.core import config as cfg
    reload(cfg)
    from app import main as app_main
    reload(app_main)
    app = app_main.create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.put("/v1/admin/retention", json={"keep_count": 12, "keep_days": 30})
        assert r.status_code == 200
        assert r.json() == {"keep_count": 12, "keep_days": 30}

    on_disk = json.loads((tmp_path / "retention.json").read_text())
    assert on_disk == {"keep_count": 12, "keep_days": 30}


@pytest.mark.asyncio
async def test_retention_put_rejects_negative(tmp_path, monkeypatch):
    monkeypatch.setenv("RANGE42_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("RANGE42_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    from importlib import reload
    from app.core import config as cfg
    reload(cfg)
    from app import main as app_main
    reload(app_main)
    app = app_main.create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.put("/v1/admin/retention", json={"keep_count": -1, "keep_days": 7})
        assert r.status_code == 422
        body = r.json()
        # Canonical error envelope (spec §18.1)
        assert body["error"] == "validation_error"
        assert any(d["field"].endswith("keep_count") for d in body["details"])


@pytest.mark.asyncio
async def test_retention_get_after_corrupt_falls_back_to_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("RANGE42_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("RANGE42_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    # Pre-seed a corrupt file.
    Path(tmp_path / "retention.json").write_text("{not json")
    from importlib import reload
    from app.core import config as cfg
    reload(cfg)
    from app import main as app_main
    reload(app_main)
    app = app_main.create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/v1/admin/retention")
        assert r.status_code == 200
        assert r.json() == {"keep_count": 5, "keep_days": 7}
