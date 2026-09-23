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


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["demo_lab"])
@pytest.mark.parametrize(
    "installation", ["missing_config", "missing_scenario", "public_only", "installed"]
)
async def test_preflight_requires_scenario_in_runner_playbooks_directory(
    tmp_path, monkeypatch, scenario, installation,
):
    """A reachable host and valid topology cannot compensate for absent playbooks.

    Use the real scenario resolver and filesystem; only external Proxmox/git
    operations are replaced. A public checkout alone is insufficient because
    the attempt runner resolves scenarios from the www-app directory.
    """
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        await _seed(
            dbmod, tmp_path, scenario=scenario,
            project_sha="deadbeef" if scenario == "_universal" else None,
            repo_owner="me", repo_name="proj",
        )
        runner_playbooks = tmp_path / "runner-playbooks"
        runner_playbooks.mkdir()
        monkeypatch.setenv("API_BACKEND_WWWAPP_PLAYBOOKS_DIR", str(runner_playbooks))
        monkeypatch.delenv("API_BACKEND_PUBLIC_PLAYBOOKS_DIR", raising=False)
        if installation == "missing_config":
            monkeypatch.delenv("API_BACKEND_WWWAPP_PLAYBOOKS_DIR")
        elif installation in ("public_only", "installed"):
            root = tmp_path / "public-playbooks" if installation == "public_only" else runner_playbooks
            entrypoint = root / "scenarios" / scenario / "main.yml"
            entrypoint.parent.mkdir(parents=True)
            entrypoint.write_text("- hosts: localhost\n  tasks: []\n")
            if installation == "public_only":
                monkeypatch.setenv("API_BACKEND_PUBLIC_PLAYBOOKS_DIR", str(root))

        from app.core.preflight import PreflightCheck
        from app.routes.v1.deployments import preflight as preflight_mod

        async def healthy_proxmox(api_url, token_ref):
            return PreflightCheck(check="proxmox_api", result="pass")

        monkeypatch.setattr(preflight_mod, "check_proxmox_api_status", healthy_proxmox)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            response = await c.post("/v1/deployments/dep-1/preflight")
            assert response.status_code == 200, response.text
            report = response.json()
            expected = "pass" if installation == "installed" else "block"
            assert report["result"] == expected, report
            playbook_checks = [
                check for check in report["checks"] if check["check"] == "scenario_playbook"
            ]
            assert len(playbook_checks) == 1, report
            check = playbook_checks[0]
            assert check["result"] == expected
            if expected == "block":
                assert check["code"] == "SCENARIO_PLAYBOOK_UNAVAILABLE"
                assert check["field_path"] == "scenario_label"
                assert scenario in check["detail"]
                assert "API_BACKEND_WWWAPP_PLAYBOOKS_DIR" in check["detail"]
            latest = await c.get("/v1/deployments/dep-1/preflight")
            assert latest.json()["checks"] == report["checks"]
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_existing_universal_preflight_explains_retirement(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        await _seed(dbmod, tmp_path, scenario="_universal", project_sha="a" * 40)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            response = await client.post("/v1/deployments/dep-1/preflight")
        assert response.status_code == 200
        assert response.json()["result"] == "block"
        assert response.json()["checks"][0]["code"] == "SCENARIO_RETIRED"
        assert not (tmp_path / "WS-_universal" / "project").exists()
    finally:
        await dbmod.dispose_engine()
