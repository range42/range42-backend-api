"""End-to-end smoke test: with all Plan A changes (T2-T11) applied,
``demo_lab`` still deploys correctly through the new v1 deploy chain.

This is a regression test confirming the legacy scenario path is not broken
by the universal-scenario plumbing added in earlier tasks.

Two scenarios are exercised:

1. ``test_demo_lab_smoke_with_capturing_runner`` — uses a tracing fake runner
   (no DetachedRunner) and asserts ``start_attempt`` produced the right
   extravars / envvars and skipped the universal-only project checkout.

2. ``test_demo_lab_smoke_with_real_detached_runner`` — uses the real
   ``DetachedRunner`` (with ``asyncio.create_subprocess_exec`` stubbed so no
   real ansible-runner CLI is spawned) to verify the full
   ``private_data_dir`` layout end-to-end: ``project/`` symlink,
   ``env/cmdline``, KEY=VALUE ``env/envvars`` (0600), and 0600 ``env/extravars``.
   A controllable ``FakeProc`` lets us inspect the artifact dir *before* the
   ``_run`` finally-block shreds the env files (T10).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Shared scaffolding
# ---------------------------------------------------------------------------

def _setup_env_and_db(tmp_path: Path, monkeypatch) -> None:
    """Set env vars and reload core.config + core.db so tests are isolated."""
    monkeypatch.setenv("RANGE42_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    monkeypatch.setenv("RANGE42_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("RANGE42_AUTO_START_ATTEMPTS", "0")


async def _build_legacy_demo_lab_attempt(tmp_path: Path, ws: Path,
                                         dep_id: str, att_id: str):
    """Construct Source / ProxmoxHost / Project / Deployment(scenario_label='demo_lab')
    / Attempt rows in a fresh sqlite DB and return the loaded ``Attempt``.

    Uses the same pattern as
    ``tests/core/test_deploy_trigger.py::test_start_attempt_legacy_scenario_does_not_clone_or_write_inventory``.
    """
    from importlib import reload

    from app.core import config as cfg, db as dbmod
    reload(cfg)
    reload(dbmod)

    from app.core.models import (
        Attempt, Base, Deployment, Project, ProxmoxHost, Source,
    )

    engine = dbmod.get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with dbmod.get_session_factory()() as s:
        s.add(Source(id="s", provider="github", base_url="u", auth_kind="none"))
        s.add(ProxmoxHost(
            id="h", name="n", api_url="u", node_name="n", token_ref="t"
        ))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Project(id="p", name="p", source_id="s",
                      branch_strategy="shared_repo_subdir"))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Deployment(
            id=dep_id, codename="WS", scenario_label="demo_lab",
            project_id="p", target_host_id="h", team_count=1,
            state="pending", workspace_path=str(ws),
        ))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Attempt(id=att_id, deployment_id=dep_id, scope="full",
                      state="pending"))
        await s.commit()

    # Return a session_factory the caller can use to load the attempt.
    return dbmod.get_session_factory()


# ---------------------------------------------------------------------------
# Test 1: tracing runner, no DetachedRunner
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_demo_lab_smoke_with_capturing_runner(tmp_path, monkeypatch):
    """Legacy ``scenario_label='demo_lab'`` reaches ``runner.start()`` via the
    new wiring without triggering universal-path project checkout / topology
    generation."""
    # ARRANGE: playbooks dir with demo_lab scenario
    pb_root = tmp_path / "playbooks"
    (pb_root / "scenarios" / "demo_lab").mkdir(parents=True)
    (pb_root / "scenarios" / "demo_lab" / "main.yml").write_text(
        "- hosts: all\n  tasks: []\n"
    )
    monkeypatch.setenv("API_BACKEND_WWWAPP_PLAYBOOKS_DIR", str(pb_root))

    _setup_env_and_db(tmp_path, monkeypatch)

    # ARRANGE: workspace with pre-rendered legacy inventory
    ws = tmp_path / "WS-demo_lab-1"
    ws.mkdir()
    (ws / "runner").mkdir()
    (ws / "ssh_keys").mkdir()
    (ws / "secrets").mkdir()
    (ws / "secrets" / "vault_pass.txt").write_text("legacy-vault-pass-DEMO\n")
    (ws / "inventory").mkdir()
    (ws / "inventory" / "hosts.yml").write_text(
        "all:\n  hosts:\n    legacy_host:\n      ansible_host: 10.0.0.5\n"
    )

    session_factory = await _build_legacy_demo_lab_attempt(
        tmp_path, ws, dep_id="dep-smoke-1", att_id="att-smoke-1",
    )

    # ARRANGE: Tracing runner that records what start_attempt forwarded.
    captured: dict = {}

    class _CaptureHandle:
        pid = 4242

        async def wait(self) -> int:
            return 0

        async def kill(self) -> None:
            pass

    class _CaptureRunner:
        async def start(self, *, private_data_dir, extravars, envvars):
            captured["private_data_dir"] = private_data_dir
            captured["extravars"] = dict(extravars)
            captured["envvars"] = dict(envvars)
            private_data_dir.mkdir(parents=True, exist_ok=True)
            return _CaptureHandle()

    # ACT
    from sqlalchemy import select
    from app.core.deploy_trigger import start_attempt
    from app.core.models import Attempt

    async with session_factory() as s:
        att = (await s.execute(
            select(Attempt).where(Attempt.id == "att-smoke-1"))
        ).scalar_one()
        await start_attempt(s, attempt=att, runner=_CaptureRunner())

    # ASSERT: legacy path was followed end-to-end
    extravars = captured["extravars"]
    assert "r42_playbook_path" in extravars
    assert extravars["r42_playbook_path"].endswith(
        "scenarios/demo_lab/main.yml"
    )
    assert "r42_topology_path" not in extravars  # universal-only key absent
    assert extravars["r42_inventory_dir"] == str(ws / "inventory")
    assert extravars["r42_deployment_id"] == "dep-smoke-1"
    assert extravars["r42_attempt_id"] == "att-smoke-1"

    # ASSERT: vault pass is wired into envvars
    envvars = captured["envvars"]
    assert envvars["ANSIBLE_VAULT_PASSWORD_FILE"] == str(
        ws / "secrets" / "vault_pass.txt"
    )
    assert envvars["RANGE42_TRACE_ID"] == "att-smoke-1"

    # ASSERT: no project/ checkout happened (universal-only behavior)
    assert not (ws / "project").exists()

    # ASSERT: private_data_dir was the standard <ws>/runner/<attempt_id>
    assert captured["private_data_dir"] == ws / "runner" / "att-smoke-1"


# ---------------------------------------------------------------------------
# Test 2: real DetachedRunner with stubbed subprocess
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_demo_lab_smoke_with_real_detached_runner(tmp_path, monkeypatch):
    """Like the smoke test above but uses the real ``DetachedRunner`` so we
    exercise the ``project/`` symlink + ``env/cmdline`` + KEY=VALUE envvars
    + 0600 perms end-to-end.

    A controllable ``FakeProc`` blocks in ``wait()`` until we release it,
    so we can inspect ``private_data_dir`` *before* the ``_run`` finally-block
    shreds the env files (T10)."""
    # ARRANGE: playbooks dir with demo_lab scenario
    pb_root = tmp_path / "playbooks"
    (pb_root / "scenarios" / "demo_lab").mkdir(parents=True)
    (pb_root / "scenarios" / "demo_lab" / "main.yml").write_text(
        "- hosts: all\n  tasks: []\n"
    )
    monkeypatch.setenv("API_BACKEND_WWWAPP_PLAYBOOKS_DIR", str(pb_root))

    _setup_env_and_db(tmp_path, monkeypatch)

    ws = tmp_path / "WS-demo_lab-2"
    ws.mkdir()
    (ws / "runner").mkdir()
    (ws / "ssh_keys").mkdir()
    (ws / "secrets").mkdir()
    (ws / "inventory").mkdir()

    session_factory = await _build_legacy_demo_lab_attempt(
        tmp_path, ws, dep_id="dep-smoke-2", att_id="att-smoke-2",
    )

    # Stub asyncio.create_subprocess_exec inside runner_detached so the real
    # ansible-runner CLI is never invoked. The FakeProc.wait() blocks on an
    # asyncio.Event so we can inspect the artifact dir before the finally
    # shreds env/envvars + env/extravars.
    from app.core import runner_detached as runner_detached_module

    release = asyncio.Event()

    class _FakeProc:
        pid = 12345
        returncode = None

        async def wait(self):
            await release.wait()
            self.returncode = 0
            return 0

    async def _fake_spawn(*args, **kwargs):
        return _FakeProc()

    monkeypatch.setattr(
        runner_detached_module.asyncio,
        "create_subprocess_exec",
        _fake_spawn,
    )

    # ACT: real DetachedRunner
    from sqlalchemy import select
    from app.core.deploy_trigger import start_attempt, _BACKGROUND_TASKS
    from app.core.models import Attempt
    from app.core.runner_detached import DetachedRunner

    async with session_factory() as s:
        att = (await s.execute(
            select(Attempt).where(Attempt.id == "att-smoke-2"))
        ).scalar_one()
        await start_attempt(s, attempt=att, runner=DetachedRunner())

    # The real DetachedRunner has now populated <ws>/runner/<att>/. The _run()
    # background task is suspended in handle.wait() (FakeProc blocks on the
    # release Event), so the env/* files have NOT yet been shredded.
    pdd = ws / "runner" / "att-smoke-2"

    # ASSERT: project/ was created (symlink to playbooks root)
    project_dir = pdd / "project"
    assert project_dir.exists(), \
        "DetachedRunner did not create project/ in private_data_dir"
    assert (project_dir / "scenarios" / "demo_lab" / "main.yml").is_file(), \
        "project/ does not point to a tree containing the demo_lab playbook"

    # ASSERT: env/cmdline references demo_lab and inventory
    cmdline = (pdd / "env" / "cmdline").read_text()
    assert "scenarios/demo_lab/main.yml" in cmdline
    assert "-i inventory" in cmdline

    # ASSERT: env/envvars is KEY=VALUE format (not JSON), 0600
    envvars_path = pdd / "env" / "envvars"
    assert envvars_path.is_file()
    envvars_content = envvars_path.read_text()
    assert not envvars_content.lstrip().startswith("{"), \
        "env/envvars must be KEY=VALUE format, not JSON"
    assert "RANGE42_TRACE_ID=att-smoke-2" in envvars_content
    mode = envvars_path.stat().st_mode & 0o777
    assert mode == 0o600, f"Expected 0o600 envvars, got {oct(mode)}"

    # ASSERT: env/extravars is 0600
    extravars_path = pdd / "env" / "extravars"
    assert extravars_path.is_file()
    mode = extravars_path.stat().st_mode & 0o777
    assert mode == 0o600, f"Expected 0o600 extravars, got {oct(mode)}"

    # ASSERT: no project/ subdir was cloned into ws/ (universal-only behavior)
    assert not (ws / "project").exists(), \
        "Legacy scenario must not write ws/project/"

    # CLEANUP: release the FakeProc so the _run background task can complete
    # and shred the env files. Awaiting it here keeps test isolation clean.
    release.set()
    pending = [t for t in _BACKGROUND_TASKS if not t.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
