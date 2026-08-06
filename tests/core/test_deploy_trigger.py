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


@pytest.mark.asyncio
async def test_start_attempt_injects_ssh_auth_sock_when_workspace_has_vault_keys(
    tmp_path, monkeypatch,
):
    """When the workspace ships a vault + passphrase-protected keys, start_attempt
    unlocks them into an ssh-agent and forwards SSH_AUTH_SOCK to the runner env,
    so ansible-runner can authenticate to the VMs from inside the container."""
    import subprocess as sp

    import yaml as _yaml

    monkeypatch.setenv("RANGE42_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    monkeypatch.setenv("RANGE42_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("RANGE42_AUTO_START_ATTEMPTS", "0")

    pb_root = tmp_path / "playbooks"
    (pb_root / "scenarios" / "demo_lab").mkdir(parents=True)
    (pb_root / "scenarios" / "demo_lab" / "main.yml").write_text(
        "- hosts: all\n  tasks: []\n")
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
    (ws / "runner").mkdir(parents=True)
    (ws / "secrets").mkdir()
    key_dir = ws / "ssh_keys" / "backend_keys"
    key_dir.mkdir(parents=True)
    vp = ws / "secrets" / "vault_pass.txt"
    vp.write_text("vaultpw")
    sp.run(["ssh-keygen", "-t", "ed25519", "-N", "kp", "-C", "k", "-f",
            str(key_dir / "r42.WS-demo_lab-deployer-key_alice")],
           check=True, capture_output=True, text=True)
    vfile = ws / "secrets" / "default_vault.yml"
    vfile.write_text(_yaml.safe_dump({"ssh_passphrase_deployer_admin": "kp"}))
    sp.run(["ansible-vault", "encrypt", "--vault-password-file", str(vp),
            str(vfile)], check=True, capture_output=True, text=True)

    async with dbmod.get_session_factory()() as s:
        s.add(Source(id="s", provider="github", base_url="u", auth_kind="none"))
        s.add(ProxmoxHost(id="h", name="n", api_url="u", node_name="n", token_ref="t"))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Project(id="p", name="p", source_id="s",
                      branch_strategy="shared_repo_subdir"))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Deployment(id="dep-k", codename="WS", scenario_label="demo_lab",
                         project_id="p", target_host_id="h", team_count=1,
                         state="pending", workspace_path=str(ws)))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Attempt(id="att-k", deployment_id="dep-k", scope="full",
                      state="pending"))
        await s.commit()

    captured: dict = {}

    class _CaptureHandle:
        pid = 4242
        async def wait(self) -> int:
            return 0
        async def kill(self) -> None:
            pass

    class _CaptureRunner:
        async def start(self, *, private_data_dir, extravars, envvars):
            captured["envvars"] = dict(envvars)
            private_data_dir.mkdir(parents=True, exist_ok=True)
            return _CaptureHandle()

    from app.core.deploy_trigger import start_attempt
    from sqlalchemy import select

    async with dbmod.get_session_factory()() as s:
        att = (await s.execute(
            select(Attempt).where(Attempt.id == "att-k"))).scalar_one()
        await start_attempt(s, attempt=att, runner=_CaptureRunner())

    import asyncio
    await asyncio.sleep(0.1)  # let the background _run() close the agent

    assert "SSH_AUTH_SOCK" in captured["envvars"]
    assert captured["envvars"]["SSH_AUTH_SOCK"]


# ---------------------------------------------------------------------------
# T8: Universal scenario path — project checkout + inventory generation
# ---------------------------------------------------------------------------
import json
import subprocess


def _make_project_repo(path: Path, topology_data: dict) -> str:
    """Create a local git repo with a topology.json at HEAD; return SHA."""
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "topology.json").write_text(json.dumps(topology_data))
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=path, check=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True, check=True,
    ).stdout.strip()
    return sha


@pytest.mark.asyncio
async def test_start_attempt_universal_writes_inventory_and_extravars(
    tmp_path, monkeypatch,
):
    """For scenario_label='_universal', start_attempt clones the project repo,
    writes the inventory, and passes both paths via extravars."""
    monkeypatch.setenv("RANGE42_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    monkeypatch.setenv("RANGE42_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("RANGE42_AUTO_START_ATTEMPTS", "0")

    pb_root = tmp_path / "playbooks"
    (pb_root / "scenarios" / "_universal").mkdir(parents=True)
    (pb_root / "scenarios" / "_universal" / "main.yml").write_text(
        "- hosts: all\n  tasks: []\n"
    )
    monkeypatch.setenv("API_BACKEND_WWWAPP_PLAYBOOKS_DIR", str(pb_root))

    # Local "remote" project repo with a minimal topology
    project_src = tmp_path / "project_src"
    minimal_topology = {
        "schema_version": "1.0",
        "kind": "gamenet",
        "id": "test-project",
        "name": "test",
        "naming_prefix": "test",
        "bridge_base": 140,
        "nodes": [
            {
                "id": "host-01", "kind": "vm", "role": "admin",
                "replication": {"scope": "shared"},
                "template_vmid": 9001, "config": {"cores": 1, "memory": 1024},
                "attachments": [],
            }
        ],
    }
    project_sha = _make_project_repo(project_src, minimal_topology)

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

    ws = tmp_path / "WS-universal"
    ws.mkdir(parents=True)
    (ws / "runner").mkdir()
    (ws / "ssh_keys").mkdir()
    (ws / "secrets").mkdir()
    (ws / "inventory").mkdir()

    # The implementation composes the repo URL as
    # "{base_url}/{repo_owner}/{repo_name}.git". To make a local file:// URL
    # resolve to a real git directory, we bare-clone project_src into a
    # sibling ending in ".git" and arrange the source / project fields so the
    # composed URL points at it.
    subprocess.run(
        ["git", "clone", "--bare", "-q",
         str(project_src), str(tmp_path / "project_src.git")],
        check=True,
    )
    parent_url = f"file://{tmp_path}"

    async with dbmod.get_session_factory()() as s:
        s.add(Source(id="s", provider="github", base_url=parent_url, auth_kind="none"))
        s.add(ProxmoxHost(
            id="h", name="n", api_url="https://10.0.0.5:8006",
            node_name="n", token_ref="t",
        ))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Project(
            id="p", name="p", source_id="s",
            branch_strategy="shared_repo_subdir",
            repo_owner=".", repo_name="project_src",
        ))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Deployment(
            id="dep-u", codename="WS", scenario_label="_universal",
            project_id="p", target_host_id="h", team_count=1, state="pending",
            workspace_path=str(ws), project_sha=project_sha,
        ))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Attempt(id="att-u", deployment_id="dep-u", scope="full",
                      state="pending"))
        await s.commit()

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
            select(Attempt).where(Attempt.id == "att-u"))).scalar_one()
        await start_attempt(s, attempt=att, runner=_CaptureRunner())

    extravars = captured["extravars"]
    assert "r42_inventory_dir" in extravars
    inv_dir = Path(extravars["r42_inventory_dir"])
    assert (inv_dir / "hosts.yml").is_file()

    # And the project was checked out
    assert "r42_topology_path" in extravars
    assert (ws / "project" / "topology.json").is_file()
    assert extravars["r42_topology_path"] == str(ws / "project" / "topology.json")


@pytest.mark.asyncio
async def test_start_attempt_legacy_scenario_does_not_clone_or_write_inventory(
    tmp_path, monkeypatch,
):
    """For non-_universal scenarios, no project checkout / inventory generation.
    Existing inventory at ws/inventory/ is reused."""
    monkeypatch.setenv("RANGE42_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    monkeypatch.setenv("RANGE42_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("RANGE42_AUTO_START_ATTEMPTS", "0")

    pb_root = tmp_path / "playbooks"
    (pb_root / "scenarios" / "demo_lab").mkdir(parents=True)
    (pb_root / "scenarios" / "demo_lab" / "main.yml").write_text(
        "- hosts: all\n  tasks: []\n"
    )
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

    ws = tmp_path / "WS-legacy"
    ws.mkdir(parents=True)
    (ws / "runner").mkdir()
    (ws / "inventory").mkdir()

    async with dbmod.get_session_factory()() as s:
        s.add(Source(id="s", provider="github", base_url="u", auth_kind="none"))
        s.add(ProxmoxHost(id="h", name="n", api_url="u", node_name="n", token_ref="t"))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Project(id="p", name="p", source_id="s",
                      branch_strategy="shared_repo_subdir"))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Deployment(
            id="dep-l", codename="WS", scenario_label="demo_lab",
            project_id="p", target_host_id="h", team_count=1, state="pending",
            workspace_path=str(ws),
        ))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Attempt(id="att-l", deployment_id="dep-l", scope="full",
                      state="pending"))
        await s.commit()

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
            select(Attempt).where(Attempt.id == "att-l"))).scalar_one()
        await start_attempt(s, attempt=att, runner=_CaptureRunner())

    # Legacy: r42_topology_path should NOT be set
    assert "r42_topology_path" not in captured["extravars"]
    # No project/ subdir should be created
    assert not (ws / "project").exists()
    # Inventory dir points to existing legacy path
    assert captured["extravars"]["r42_inventory_dir"].endswith("/inventory")


# ---------------------------------------------------------------------------
# Guards: nullable project_sha / repo_owner / repo_name on _universal scenarios
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_attempt_universal_requires_project_sha(tmp_path, monkeypatch):
    """For _universal scenario, missing project_sha raises ProjectCheckoutError."""
    monkeypatch.setenv("RANGE42_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    monkeypatch.setenv("RANGE42_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("RANGE42_AUTO_START_ATTEMPTS", "0")

    pb_root = tmp_path / "playbooks"
    (pb_root / "scenarios" / "_universal").mkdir(parents=True)
    (pb_root / "scenarios" / "_universal" / "main.yml").write_text(
        "- hosts: all\n  tasks: []\n"
    )
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

    ws = tmp_path / "WS-no-sha"
    ws.mkdir(parents=True)
    (ws / "runner").mkdir()
    (ws / "ssh_keys").mkdir()
    (ws / "secrets").mkdir()
    (ws / "inventory").mkdir()

    async with dbmod.get_session_factory()() as s:
        s.add(Source(id="s", provider="github", base_url="https://github.com",
                     auth_kind="none"))
        s.add(ProxmoxHost(
            id="h", name="n", api_url="https://10.0.0.5:8006",
            node_name="n", token_ref="t",
        ))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Project(
            id="p", name="p", source_id="s",
            branch_strategy="shared_repo_subdir",
            repo_owner="me", repo_name="proj",
        ))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        # project_sha intentionally NOT set
        s.add(Deployment(
            id="dep-nosha", codename="WS", scenario_label="_universal",
            project_id="p", target_host_id="h", team_count=1, state="pending",
            workspace_path=str(ws),
        ))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Attempt(id="att-nosha", deployment_id="dep-nosha", scope="full",
                      state="pending"))
        await s.commit()

    class _NoOpHandle:
        pid = 1

        async def wait(self) -> int:
            return 0

        async def kill(self) -> None:
            pass

    class _NoOpRunner:
        async def start(self, *, private_data_dir, extravars, envvars):
            private_data_dir.mkdir(parents=True, exist_ok=True)
            return _NoOpHandle()

    from app.core.deploy_trigger import start_attempt
    from app.core.errors import ProjectCheckoutError
    from sqlalchemy import select

    async with dbmod.get_session_factory()() as s:
        att = (await s.execute(
            select(Attempt).where(Attempt.id == "att-nosha"))).scalar_one()
        with pytest.raises(ProjectCheckoutError) as exc:
            await start_attempt(s, attempt=att, runner=_NoOpRunner())
        assert "project_sha" in str(exc.value.message)


@pytest.mark.asyncio
async def test_start_attempt_universal_requires_repo_owner_and_name(
    tmp_path, monkeypatch,
):
    """For _universal scenario, missing repo_owner or repo_name raises ProjectCheckoutError."""
    monkeypatch.setenv("RANGE42_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    monkeypatch.setenv("RANGE42_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("RANGE42_AUTO_START_ATTEMPTS", "0")

    pb_root = tmp_path / "playbooks"
    (pb_root / "scenarios" / "_universal").mkdir(parents=True)
    (pb_root / "scenarios" / "_universal" / "main.yml").write_text(
        "- hosts: all\n  tasks: []\n"
    )
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

    ws = tmp_path / "WS-noowner"
    ws.mkdir(parents=True)
    (ws / "runner").mkdir()
    (ws / "ssh_keys").mkdir()
    (ws / "secrets").mkdir()
    (ws / "inventory").mkdir()

    async with dbmod.get_session_factory()() as s:
        s.add(Source(id="s", provider="github", base_url="https://github.com",
                     auth_kind="none"))
        s.add(ProxmoxHost(
            id="h", name="n", api_url="https://10.0.0.5:8006",
            node_name="n", token_ref="t",
        ))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        # repo_owner/repo_name intentionally NOT set
        s.add(Project(
            id="p", name="p", source_id="s",
            branch_strategy="shared_repo_subdir",
        ))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Deployment(
            id="dep-noowner", codename="WS", scenario_label="_universal",
            project_id="p", target_host_id="h", team_count=1, state="pending",
            workspace_path=str(ws),
            project_sha="0000000000000000000000000000000000000000",
        ))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Attempt(id="att-noowner", deployment_id="dep-noowner", scope="full",
                      state="pending"))
        await s.commit()

    class _NoOpHandle:
        pid = 1

        async def wait(self) -> int:
            return 0

        async def kill(self) -> None:
            pass

    class _NoOpRunner:
        async def start(self, *, private_data_dir, extravars, envvars):
            private_data_dir.mkdir(parents=True, exist_ok=True)
            return _NoOpHandle()

    from app.core.deploy_trigger import start_attempt
    from app.core.errors import ProjectCheckoutError
    from sqlalchemy import select

    async with dbmod.get_session_factory()() as s:
        att = (await s.execute(
            select(Attempt).where(Attempt.id == "att-noowner"))).scalar_one()
        with pytest.raises(ProjectCheckoutError) as exc:
            await start_attempt(s, attempt=att, runner=_NoOpRunner())
        assert "repo_owner" in str(exc.value.message) or "repo_name" in str(exc.value.message)


# ---------------------------------------------------------------------------
# ssh-agent lifetime: an agent holding unlocked private keys must never
# outlive the attempt that started it.
# ---------------------------------------------------------------------------


class _FakeAgent:
    """Stand-in for SshAgentHandle that records close()."""

    def __init__(self) -> None:
        self.env = {"SSH_AUTH_SOCK": "/tmp/fake.sock"}
        self.closed = False

    def close(self) -> None:
        self.closed = True


async def _seed_deploy(tmp_path, monkeypatch, *, scenario, dep_id, att_id,
                       with_sha=True):
    """Minimal DB + workspace fixture; returns the workspace path."""
    monkeypatch.setenv("RANGE42_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    monkeypatch.setenv("RANGE42_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("RANGE42_AUTO_START_ATTEMPTS", "0")

    pb_root = tmp_path / "playbooks"
    (pb_root / "scenarios" / scenario).mkdir(parents=True, exist_ok=True)
    (pb_root / "scenarios" / scenario / "main.yml").write_text(
        "- hosts: all\n  tasks: []\n")
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

    ws = tmp_path / f"WS-{scenario}"
    for sub in ("runner", "secrets", "ssh_keys", "inventory"):
        (ws / sub).mkdir(parents=True, exist_ok=True)
    # vault_pass.txt present => start_attempt takes the unlock branch
    (ws / "secrets" / "vault_pass.txt").write_text("vaultpw")

    async with dbmod.get_session_factory()() as s:
        s.add(Source(id="s", provider="github", base_url="u", auth_kind="none"))
        s.add(ProxmoxHost(id="h", name="n", api_url="u", node_name="n",
                          token_ref="t"))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Project(id="p", name="p", source_id="s",
                      branch_strategy="shared_repo_subdir",
                      repo_owner="me", repo_name="proj"))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Deployment(
            id=dep_id, codename="WS", scenario_label=scenario, project_id="p",
            target_host_id="h", team_count=1, state="pending",
            workspace_path=str(ws),
            **({"project_sha": "deadbeef"} if with_sha else {}),
        ))
        await s.commit()
    async with dbmod.get_session_factory()() as s:
        s.add(Attempt(id=att_id, deployment_id=dep_id, scope="full",
                      state="pending"))
        await s.commit()
    return dbmod


@pytest.mark.asyncio
async def test_start_attempt_does_not_unlock_keys_before_it_can_fail(
    tmp_path, monkeypatch,
):
    """The unlock must happen after every raise site.

    An agent started earlier would survive a failed attempt holding unlocked
    private keys, with nothing to reap it — and a bad project_sha is an
    ordinary user error, so failures repeat.
    """
    dbmod = await _seed_deploy(tmp_path, monkeypatch, scenario="_universal",
                               dep_id="dep-leak", att_id="att-leak",
                               with_sha=False)

    called = []
    monkeypatch.setattr(
        "app.core.deploy_trigger.unlock_workspace_keys",
        lambda ws, vp: called.append(ws) or _FakeAgent(),
    )

    class _NoOpRunner:
        async def start(self, *, private_data_dir, extravars, envvars):
            raise AssertionError("should not reach the runner")

    from app.core.deploy_trigger import start_attempt
    from app.core.errors import ProjectCheckoutError
    from app.core.models import Attempt
    from sqlalchemy import select

    async with dbmod.get_session_factory()() as s:
        att = (await s.execute(
            select(Attempt).where(Attempt.id == "att-leak"))).scalar_one()
        with pytest.raises(ProjectCheckoutError):
            await start_attempt(s, attempt=att, runner=_NoOpRunner())

    assert called == [], "ssh-agent was started before the attempt could fail"


@pytest.mark.asyncio
async def test_start_attempt_closes_ssh_agent_when_runner_start_fails(
    tmp_path, monkeypatch,
):
    """runner.start() is the last window; a failure there must kill the agent."""
    dbmod = await _seed_deploy(tmp_path, monkeypatch, scenario="demo_lab",
                               dep_id="dep-rs", att_id="att-rs")

    agent = _FakeAgent()
    monkeypatch.setattr("app.core.deploy_trigger.unlock_workspace_keys",
                        lambda ws, vp: agent)

    class _BoomRunner:
        async def start(self, *, private_data_dir, extravars, envvars):
            assert envvars["SSH_AUTH_SOCK"] == "/tmp/fake.sock"
            raise RuntimeError("runner exploded")

    from app.core.deploy_trigger import start_attempt
    from app.core.models import Attempt
    from sqlalchemy import select

    async with dbmod.get_session_factory()() as s:
        att = (await s.execute(
            select(Attempt).where(Attempt.id == "att-rs"))).scalar_one()
        with pytest.raises(RuntimeError):
            await start_attempt(s, attempt=att, runner=_BoomRunner())

    assert agent.closed, "ssh-agent leaked when runner.start() failed"
