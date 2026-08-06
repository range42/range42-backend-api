"""End-to-end smoke test: with all Plan A + Plan B changes applied, a
``_universal`` deployment correctly clones the project repo (stubbed),
reads ``topology.json``, generates ``inventory/hosts.yml``, and forwards
the right extravars / envvars through the new v1 deploy chain.

Two scenarios are exercised:

1. ``test_universal_smoke_with_capturing_runner`` — uses a tracing fake
   runner (no DetachedRunner) and asserts ``start_attempt`` produced the
   right extravars / envvars, that ``ws/project/topology.json`` was
   written by the stubbed ``checkout_project``, and that
   ``inventory/hosts.yml`` was rendered by the real ``inventory_writer``.

2. ``test_universal_smoke_with_real_detached_runner`` — uses the real
   ``DetachedRunner`` (with ``asyncio.create_subprocess_exec`` stubbed so
   no real ansible-runner CLI is spawned) to verify the full
   ``private_data_dir`` layout end-to-end: ``project/`` symlink,
   ``inventory/`` symlink, ``env/cmdline``, KEY=VALUE ``env/envvars``
   (0600), and 0600 ``env/extravars``. A controllable ``FakeProc`` lets
   us inspect the artifact dir *before* the ``_run`` finally-block shreds
   the env files (T10).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Shared scaffolding
# ---------------------------------------------------------------------------

# Minimal but well-formed topology fixture used by both tests.
_TOPOLOGY_FIXTURE: dict = {
    "version": "1.0",
    "naming_prefix": "test",
    "bridge_base": 140,
    "nodes": [
        {
            "id": "n1",
            "kind": "vm",
            "role": "admin",
            "replication": {"scope": "shared"},
            "vmid_base": 5000,
            "template_vmid": 9001,
            "config": {"cores": 2, "memory_mb": 2048},
            "attachments": [],
        }
    ],
}


def _setup_env_and_db(tmp_path: Path, monkeypatch) -> None:
    """Set env vars and reload core.config + core.db so tests are isolated.

    Mirrors the helper of the same name in
    ``tests/integration/test_legacy_deploy_smoke.py``.
    """
    monkeypatch.setenv("RANGE42_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    monkeypatch.setenv("RANGE42_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("RANGE42_AUTO_START_ATTEMPTS", "0")


async def _build_universal_attempt(tmp_path: Path, ws: Path,
                                   dep_id: str, att_id: str):
    """Construct Source / ProxmoxHost / Project / Deployment(scenario_label='_universal')
    / Attempt rows in a fresh sqlite DB and return the loaded ``Attempt``.

    Project carries ``repo_owner='me'`` / ``repo_name='proj'`` so the
    deploy_trigger composes a non-empty repo URL when calling the stubbed
    ``checkout_project``.
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
        s.add(Source(id="s", provider="github",
                     base_url="https://example.invalid",
                     auth_kind="none"))
        s.add(ProxmoxHost(
            id="h", name="n", api_url="https://10.0.0.5:8006",
            node_name="n", token_ref="root@pam!range42-backend=secret123",
        ))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Project(id="p", name="p", source_id="s",
                      branch_strategy="shared_repo_subdir",
                      repo_owner="me", repo_name="proj"))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Deployment(
            id=dep_id, codename="WS", scenario_label="_universal",
            project_id="p", target_host_id="h", team_count=1,
            project_sha="abc123",
            state="pending", workspace_path=str(ws),
        ))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Attempt(id=att_id, deployment_id=dep_id, scope="full",
                      state="pending"))
        await s.commit()

    return dbmod.get_session_factory()


def _install_fake_checkout_project(monkeypatch, topology: dict):
    """Stub the ``checkout_project`` symbol imported at module level in
    ``app.core.deploy_trigger`` so it writes a known-good ``topology.json``
    into the dest dir and returns the path. No real git is invoked.
    """
    from app.core import deploy_trigger as deploy_trigger_module

    def _fake_checkout_project(*, repo_url, sha, dest, token):
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        topology_path = dest / "topology.json"
        topology_path.write_text(json.dumps(topology))
        return topology_path

    monkeypatch.setattr(
        deploy_trigger_module, "checkout_project", _fake_checkout_project,
    )


# ---------------------------------------------------------------------------
# Test 1: tracing runner, no DetachedRunner
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_universal_smoke_with_capturing_runner(tmp_path, monkeypatch):
    """``scenario_label='_universal'`` reaches ``runner.start()`` via the
    new wiring with the stubbed project checkout dropping a topology and
    the real ``inventory_writer`` rendering ``hosts.yml``."""
    # ARRANGE: playbooks dir with _universal scenario
    pb_root = tmp_path / "playbooks"
    (pb_root / "scenarios" / "_universal").mkdir(parents=True)
    (pb_root / "scenarios" / "_universal" / "main.yml").write_text(
        "- hosts: all\n  tasks: []\n"
    )
    monkeypatch.setenv("API_BACKEND_WWWAPP_PLAYBOOKS_DIR", str(pb_root))

    _setup_env_and_db(tmp_path, monkeypatch)

    # ARRANGE: workspace (the universal path will create project/ and
    # populate inventory/hosts.yml itself, so we only seed the directories
    # start_attempt expects to exist).
    ws = tmp_path / "WS-_universal-1"
    ws.mkdir()
    (ws / "runner").mkdir()
    (ws / "ssh_keys").mkdir()
    (ws / "secrets").mkdir()
    (ws / "secrets" / "vault_pass.txt").write_text("universal-vault-pass\n")

    session_factory = await _build_universal_attempt(
        tmp_path, ws, dep_id="dep-smoke-u1", att_id="att-smoke-u1",
    )

    # Stub the project checkout. The real inventory_writer is allowed to
    # run for real — the topology fixture is small and the writer is fast,
    # plus we want to assert hosts.yml is on disk afterwards.
    _install_fake_checkout_project(monkeypatch, _TOPOLOGY_FIXTURE)

    # ARRANGE: tracing runner that records what start_attempt forwarded.
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
            select(Attempt).where(Attempt.id == "att-smoke-u1"))
        ).scalar_one()
        await start_attempt(s, attempt=att, runner=_CaptureRunner())

    # ASSERT: universal-only extravars are populated
    extravars = captured["extravars"]
    assert "r42_topology_path" in extravars
    assert extravars["r42_topology_path"].endswith("topology.json")
    assert extravars["r42_inventory_dir"] == str(ws / "inventory")
    assert extravars["r42_deployment_id"] == "dep-smoke-u1"
    assert extravars["r42_attempt_id"] == "att-smoke-u1"
    assert "r42_playbook_path" in extravars
    assert extravars["r42_playbook_path"].endswith(
        "scenarios/_universal/main.yml"
    )

    # ASSERT: Proxmox API creds for the playbook's node-network tasks are
    # derived from the host (token_ref format: user!tokenid=secret).
    assert extravars["proxmox_api_host"] == "10.0.0.5:8006"
    assert extravars["proxmox_node"] == "n"
    assert extravars["proxmox_api_user"] == "root@pam"
    assert extravars["proxmox_api_token_id"] == "range42-backend"
    assert extravars["proxmox_api_token_secret"] == "secret123"

    # ASSERT: the stubbed checkout dropped topology.json into ws/project/
    assert (ws / "project" / "topology.json").is_file(), \
        "checkout_project stub did not write topology.json"

    # ASSERT: the real inventory_writer rendered hosts.yml from the topology
    inventory_file = ws / "inventory" / "hosts.yml"
    assert inventory_file.is_file(), \
        "inventory_writer did not render inventory/hosts.yml"
    inventory_content = inventory_file.read_text()
    # Naming: prefix=test, role=admin (group r42_admin), shared scope -> "r42.test-n1"
    assert "r42.test-n1" in inventory_content, (
        "expected hostname 'r42.test-n1' in rendered inventory; got:\n"
        f"{inventory_content}"
    )

    # ASSERT: vault pass + trace id wired into envvars
    envvars = captured["envvars"]
    assert envvars["ANSIBLE_VAULT_PASSWORD_FILE"] == str(
        ws / "secrets" / "vault_pass.txt"
    )
    assert envvars["RANGE42_TRACE_ID"] == "att-smoke-u1"

    # ASSERT: private_data_dir was the standard <ws>/runner/<attempt_id>
    assert captured["private_data_dir"] == ws / "runner" / "att-smoke-u1"


# ---------------------------------------------------------------------------
# Test 2: real DetachedRunner with stubbed subprocess
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_universal_smoke_with_real_detached_runner(tmp_path, monkeypatch):
    """Like the smoke test above but uses the real ``DetachedRunner`` so we
    exercise the ``project/`` symlink + ``inventory/`` symlink +
    ``env/cmdline`` + KEY=VALUE envvars + 0600 perms end-to-end.

    ``asyncio.create_subprocess_exec`` is stubbed so no real ansible-runner
    CLI is spawned. A controllable ``FakeProc`` blocks in ``wait()`` until
    we release it, so we can inspect ``private_data_dir`` *before* the
    ``_run`` finally-block shreds the env files (T10).
    """
    # ARRANGE: playbooks dir with _universal scenario
    pb_root = tmp_path / "playbooks"
    (pb_root / "scenarios" / "_universal").mkdir(parents=True)
    (pb_root / "scenarios" / "_universal" / "main.yml").write_text(
        "- hosts: all\n  tasks: []\n"
    )
    monkeypatch.setenv("API_BACKEND_WWWAPP_PLAYBOOKS_DIR", str(pb_root))

    _setup_env_and_db(tmp_path, monkeypatch)

    ws = tmp_path / "WS-_universal-2"
    ws.mkdir()
    (ws / "runner").mkdir()
    (ws / "ssh_keys").mkdir()
    (ws / "secrets").mkdir()

    session_factory = await _build_universal_attempt(
        tmp_path, ws, dep_id="dep-smoke-u2", att_id="att-smoke-u2",
    )

    _install_fake_checkout_project(monkeypatch, _TOPOLOGY_FIXTURE)

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
            select(Attempt).where(Attempt.id == "att-smoke-u2"))
        ).scalar_one()
        await start_attempt(s, attempt=att, runner=DetachedRunner())

    # The real DetachedRunner has now populated <ws>/runner/<att>/. The _run()
    # background task is suspended in handle.wait() (FakeProc blocks on the
    # release Event), so the env/* files have NOT yet been shredded.
    pdd = ws / "runner" / "att-smoke-u2"

    # ASSERT: project/ was created (symlink to playbooks root)
    project_dir = pdd / "project"
    assert project_dir.exists(), \
        "DetachedRunner did not create project/ in private_data_dir"
    assert (project_dir / "scenarios" / "_universal" / "main.yml").is_file(), \
        "project/ does not point to a tree containing the _universal playbook"

    # ASSERT: inventory/ symlink in private_data_dir resolves to the
    # rendered hosts.yml under ws/inventory/.
    inventory_dir = pdd / "inventory"
    assert inventory_dir.exists(), \
        "DetachedRunner did not create inventory/ in private_data_dir"
    assert (inventory_dir / "hosts.yml").is_file(), \
        "inventory/hosts.yml not visible through DetachedRunner's view"

    # ASSERT: project/ checkout actually happened in the workspace
    assert (ws / "project" / "topology.json").is_file(), \
        "checkout_project stub did not write topology.json"

    # ASSERT: env/cmdline references _universal and inventory
    cmdline = (pdd / "env" / "cmdline").read_text()
    assert "scenarios/_universal/main.yml" in cmdline
    assert "-i inventory" in cmdline

    # ASSERT: env/envvars is KEY=VALUE format (not JSON), 0600
    envvars_path = pdd / "env" / "envvars"
    assert envvars_path.is_file()
    envvars_content = envvars_path.read_text()
    assert not envvars_content.lstrip().startswith("{"), \
        "env/envvars must be KEY=VALUE format, not JSON"
    assert "RANGE42_TRACE_ID=att-smoke-u2" in envvars_content
    mode = envvars_path.stat().st_mode & 0o777
    assert mode == 0o600, f"Expected 0o600 envvars, got {oct(mode)}"

    # ASSERT: env/extravars is 0600
    extravars_path = pdd / "env" / "extravars"
    assert extravars_path.is_file()
    mode = extravars_path.stat().st_mode & 0o777
    assert mode == 0o600, f"Expected 0o600 extravars, got {oct(mode)}"

    # CLEANUP: release the FakeProc so the _run background task can complete
    # and shred the env files. Awaiting it here keeps test isolation clean.
    release.set()
    pending = [t for t in _BACKGROUND_TASKS if not t.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
