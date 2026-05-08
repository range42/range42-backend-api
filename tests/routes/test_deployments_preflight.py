"""/v1/deployments/:id/preflight tests."""
import json

import pytest
from httpx import ASGITransport, AsyncClient


async def _boot(tmp_path, monkeypatch):
    monkeypatch.setenv("RANGE42_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
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
    return create_app(), dbmod


async def _seed(dbmod, tmp_path, codename="X", scenario="y", *,
                project_sha=None, team_count=1, repo_owner=None,
                repo_name=None):
    from app.core.models import Deployment, Project, ProxmoxHost, Source
    async with dbmod.get_session_factory()() as s:
        s.add(Source(id="s", provider="github", base_url="https://github.com",
                     auth_kind="none"))
        s.add(ProxmoxHost(id="h", name="n", api_url="http://127.0.0.1:1",
                          node_name="n", token_ref="t"))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Project(id="p", name="p", source_id="s",
                      branch_strategy="shared_repo_subdir",
                      repo_owner=repo_owner, repo_name=repo_name))
        await s.commit()
    ws = tmp_path / f"{codename}-{scenario}"
    ws.mkdir(parents=True, exist_ok=True)
    async with dbmod.get_session_factory()() as s:
        s.add(Deployment(id="dep-1", codename=codename, scenario_label=scenario,
                         project_id="p", target_host_id="h",
                         team_count=team_count,
                         project_sha=project_sha,
                         state="pending", workspace_path=str(ws)))
        await s.commit()
    return ws


def _good_topology() -> dict:
    """Minimal topology that should pass topology_assets, topology_node_role,
    and vmid_collision checks."""
    return {
        "nodes": [
            {
                "id": "n1",
                "kind": "vm",
                "role": "admin",
                "replication": {"scope": "shared"},
                "vmid_base": 5000,
                "attachments": [
                    {"source": {"kind": "inline_yaml",
                                "content": "- name: x\n  debug: msg=hi"},
                     "stage": "configure"},
                ],
            }
        ]
    }


def _stub_checkout_project_writing(topology: dict):
    """Return a sync stub for ``checkout_project`` that writes ``topology.json``
    into ``dest`` and returns the path. Mirrors the real signature."""
    def _stub(*, repo_url, sha, dest, token):
        from pathlib import Path
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        topo_path = dest / "topology.json"
        topo_path.write_text(json.dumps(topology))
        return topo_path
    return _stub


@pytest.mark.asyncio
async def test_preflight_round_trip(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        await _seed(dbmod, tmp_path)
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as c:
            r = await c.post("/v1/deployments/dep-1/preflight")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["result"] in ("pass", "warn", "block")
            assert isinstance(body["checks"], list)
            assert any(c["check"] == "proxmox_api" for c in body["checks"])
            r = await c.get("/v1/deployments/dep-1/preflight")
            assert r.status_code == 200
            assert r.json()["deployment_id"] == "dep-1"
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_preflight_get_404_when_no_record(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        await _seed(dbmod, tmp_path)
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as c:
            r = await c.get("/v1/deployments/dep-1/preflight")
            assert r.status_code == 404
            assert r.json()["code"] == "NOT_FOUND"
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_preflight_post_404_on_missing_deployment(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as c:
            r = await c.post("/v1/deployments/bogus/preflight")
            assert r.status_code == 404
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_preflight_universal_loads_topology_and_runs_topology_checks(
    tmp_path, monkeypatch,
):
    """For ``_universal``, the route clones the project (stubbed),
    reads topology.json, and runs the new topology-aware checks."""
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        await _seed(
            dbmod, tmp_path,
            scenario="_universal",
            project_sha="deadbeef",
            team_count=2,
            repo_owner="me", repo_name="proj",
        )
        # Stub checkout_project on the route module so no real git runs.
        from app.routes.v1.deployments import preflight as preflight_mod
        monkeypatch.setattr(
            preflight_mod, "checkout_project",
            _stub_checkout_project_writing(_good_topology()),
        )

        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as c:
            r = await c.post("/v1/deployments/dep-1/preflight")
            assert r.status_code == 200, r.text
            body = r.json()
            checks = body["checks"]
            # All three new topology-aware checks should be present and pass.
            role_checks = [c for c in checks if c["check"] == "topology_node_role"]
            assert role_checks and all(c["result"] == "pass" for c in role_checks), \
                role_checks
            asset_checks = [c for c in checks if c["check"] == "topology_assets"]
            assert asset_checks and all(c["result"] == "pass" for c in asset_checks), \
                asset_checks
            vmid_checks = [c for c in checks if c["check"] == "vmid_collision"]
            assert vmid_checks and all(c["result"] == "pass" for c in vmid_checks), \
                vmid_checks
            # No legacy placeholder checks should appear in _universal mode.
            assert not any(c["check"] == "secret_completeness" for c in checks)
            assert not any(c["check"] == "resource_budget" for c in checks)
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_preflight_universal_blocks_on_missing_role(tmp_path, monkeypatch):
    """A topology with a VM/LXC node missing ``role`` produces a
    ``topology_node_role`` block check."""
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        await _seed(
            dbmod, tmp_path,
            scenario="_universal",
            project_sha="deadbeef",
            team_count=1,
            repo_owner="me", repo_name="proj",
        )
        bad_topology = _good_topology()
        bad_topology["nodes"][0].pop("role")
        from app.routes.v1.deployments import preflight as preflight_mod
        monkeypatch.setattr(
            preflight_mod, "checkout_project",
            _stub_checkout_project_writing(bad_topology),
        )

        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as c:
            r = await c.post("/v1/deployments/dep-1/preflight")
            assert r.status_code == 200, r.text
            body = r.json()
            blocks = [
                c for c in body["checks"]
                if c["check"] == "topology_node_role" and c["result"] == "block"
            ]
            assert blocks, body["checks"]
            assert blocks[0]["code"] == "TOPOLOGY_NODE_MISSING_ROLE"
            assert body["result"] == "block"
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_preflight_universal_blocks_on_project_checkout_failure(
    tmp_path, monkeypatch,
):
    """If ``checkout_project`` raises ``ProjectCheckoutError``, the report
    surfaces a ``topology_load`` block with ``PROJECT_CHECKOUT_FAILED``
    instead of crashing the route."""
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        await _seed(
            dbmod, tmp_path,
            scenario="_universal",
            project_sha="deadbeef",
            team_count=1,
            repo_owner="me", repo_name="proj",
        )

        from app.core.errors import ProjectCheckoutError
        from app.routes.v1.deployments import preflight as preflight_mod

        def _boom(*, repo_url, sha, dest, token):
            raise ProjectCheckoutError(message="repo not found")

        monkeypatch.setattr(preflight_mod, "checkout_project", _boom)

        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as c:
            r = await c.post("/v1/deployments/dep-1/preflight")
            assert r.status_code == 200, r.text
            body = r.json()
            loads = [
                c for c in body["checks"]
                if c["check"] == "topology_load" and c["result"] == "block"
            ]
            assert loads, body["checks"]
            assert loads[0]["code"] == "PROJECT_CHECKOUT_FAILED"
            assert "repo not found" in loads[0]["detail"]
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_preflight_legacy_scenario_unchanged(tmp_path, monkeypatch):
    """Legacy (non-_universal) scenarios run only the original placeholder
    checks; no ``topology_*`` checks are emitted."""
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        await _seed(dbmod, tmp_path, scenario="demo_lab")
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as c:
            r = await c.post("/v1/deployments/dep-1/preflight")
            assert r.status_code == 200, r.text
            body = r.json()
            check_names = {c["check"] for c in body["checks"]}
            assert not any(n.startswith("topology_") for n in check_names), \
                check_names
            # Legacy placeholders are still present.
            assert "secret_completeness" in check_names
            assert "resource_budget" in check_names
            assert "vmid_collision" in check_names
    finally:
        await dbmod.dispose_engine()
