"""Unit tests for the scenario_label -> playbook path resolver.

Tests the _resolve_playbook_for_scenario() helper added to deploy_trigger
in support of Task 5: wire the resolver into start_attempt() so the
DetachedRunner sees r42_playbook_path in extravars.
"""
from pathlib import Path

import pytest


def test_resolve_playbook_for_universal(tmp_path, monkeypatch):
    """scenario_label='_universal' resolves to <playbooks_dir>/scenarios/_universal/main.yml."""
    pb_root = tmp_path / "playbooks"
    (pb_root / "scenarios" / "_universal").mkdir(parents=True)
    (pb_root / "scenarios" / "_universal" / "main.yml").write_text("- hosts: all\n")

    # The resolver reads API_BACKEND_WWWAPP_PLAYBOOKS_DIR via os.getenv()
    # at call time (not from settings), so a setenv is sufficient.
    monkeypatch.setenv("API_BACKEND_WWWAPP_PLAYBOOKS_DIR", str(pb_root))

    from app.core.deploy_trigger import _resolve_playbook_for_scenario
    resolved = _resolve_playbook_for_scenario("_universal")
    assert resolved == (pb_root / "scenarios" / "_universal" / "main.yml").resolve()


def test_resolve_playbook_for_demo_lab(tmp_path, monkeypatch):
    """Existing scenario_label='demo_lab' must keep working (backwards-compat)."""
    pb_root = tmp_path / "playbooks"
    (pb_root / "scenarios" / "demo_lab").mkdir(parents=True)
    (pb_root / "scenarios" / "demo_lab" / "main.yml").write_text("- hosts: all\n")

    monkeypatch.setenv("API_BACKEND_WWWAPP_PLAYBOOKS_DIR", str(pb_root))

    from app.core.deploy_trigger import _resolve_playbook_for_scenario
    resolved = _resolve_playbook_for_scenario("demo_lab")
    assert resolved.name == "main.yml"
    assert "demo_lab" in str(resolved)


def test_resolve_playbook_rejects_traversal(tmp_path, monkeypatch):
    """Path traversal attempts must be rejected by the existing regex validation."""
    pb_root = tmp_path / "playbooks"
    pb_root.mkdir()
    monkeypatch.setenv("API_BACKEND_WWWAPP_PLAYBOOKS_DIR", str(pb_root))

    from app.core.deploy_trigger import _resolve_playbook_for_scenario
    # The existing resolver raises HTTPException(400); accept any exception subclass.
    with pytest.raises(Exception):
        _resolve_playbook_for_scenario("../etc/passwd")


@pytest.mark.asyncio
async def test_start_attempt_passes_playbook_path_in_extravars(tmp_path, monkeypatch):
    """start_attempt() sets r42_playbook_path in extravars based on scenario_label.

    This is the wiring the DetachedRunner (T2-T4) relies on: when the runner
    receives r42_playbook_path it can populate project/, env/cmdline, and
    env/envvars from it.
    """
    monkeypatch.setenv("RANGE42_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    monkeypatch.setenv("RANGE42_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("RANGE42_AUTO_START_ATTEMPTS", "0")

    pb_root = tmp_path / "playbooks"
    (pb_root / "scenarios" / "demo_lab").mkdir(parents=True)
    (pb_root / "scenarios" / "demo_lab" / "main.yml").write_text("- hosts: all\n  tasks: []\n")
    monkeypatch.setenv("API_BACKEND_WWWAPP_PLAYBOOKS_DIR", str(pb_root))

    from importlib import reload
    from app.core import config as cfg, db as dbmod
    reload(cfg)
    reload(dbmod)
    from app.core.models import (
        Base, Source, ProxmoxHost, Project, Deployment, Attempt,
    )
    engine = dbmod.get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    ws = tmp_path / "WS-demo_lab"
    ws.mkdir(parents=True)
    (ws / "runner").mkdir()

    async with dbmod.get_session_factory()() as s:
        s.add(Source(id="s", provider="github", base_url="u", auth_kind="none"))
        s.add(ProxmoxHost(id="h", name="n", api_url="u", node_name="n", token_ref="t"))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Project(id="p", name="p", source_id="s",
                      branch_strategy="shared_repo_subdir"))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Deployment(id="dep-x", codename="WS", scenario_label="demo_lab",
                         project_id="p", target_host_id="h", team_count=1,
                         state="pending", workspace_path=str(ws)))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Attempt(id="att-x", deployment_id="dep-x", scope="full",
                      state="pending"))
        await s.commit()

    # Capturing fake runner — does not exec, only records what start_attempt
    # forwarded into extravars.
    captured: dict = {}

    class _CaptureHandle:
        pid = 4242
        async def wait(self) -> int:
            return 0
        async def kill(self) -> None:
            pass

    class _CaptureRunner:
        async def start(self, *, private_data_dir, extravars, envvars):
            captured["extravars"] = dict(extravars)
            captured["envvars"] = dict(envvars)
            private_data_dir.mkdir(parents=True, exist_ok=True)
            return _CaptureHandle()

    from app.core.deploy_trigger import start_attempt
    from sqlalchemy import select

    async with dbmod.get_session_factory()() as s:
        att = (await s.execute(
            select(Attempt).where(Attempt.id == "att-x"))).scalar_one()
        await start_attempt(s, attempt=att, runner=_CaptureRunner())

    expected = (pb_root / "scenarios" / "demo_lab" / "main.yml").resolve()
    assert "r42_playbook_path" in captured["extravars"]
    assert captured["extravars"]["r42_playbook_path"] == str(expected)
    # And the existing extravars are still there (no regression):
    assert captured["extravars"]["r42_deployment_id"] == "dep-x"
    assert captured["extravars"]["r42_attempt_id"] == "att-x"
