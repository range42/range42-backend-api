import json
from pathlib import Path
import pytest


@pytest.mark.asyncio
async def test_check_topology_assets_passes_when_all_resolved(tmp_path):
    """All attachments resolve → all checks pass."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "ansible").mkdir()
    (project_dir / "ansible" / "configure.yml").write_text("- hosts: all\n")

    topology = {
        "nodes": [
            {"id": "n1", "kind": "vm", "role": "admin", "replication": {"scope": "shared"},
             "attachments": [
                 {"source": {"kind": "file_upload", "path": "ansible/configure.yml"}, "stage": "configure"},
                 {"source": {"kind": "inline_yaml", "content": "- name: x\n  debug: msg=hi"}, "stage": "configure"},
             ]}
        ]
    }
    from app.core.preflight import check_topology_assets
    checks = await check_topology_assets(project_dir, None, topology)
    assert all(c.result == "pass" for c in checks), [c for c in checks if c.result != "pass"]


@pytest.mark.asyncio
async def test_check_topology_assets_blocks_missing_file_upload(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    topology = {
        "nodes": [
            {"id": "n1", "kind": "vm", "role": "admin", "replication": {"scope": "shared"},
             "attachments": [
                 {"source": {"kind": "file_upload", "path": "missing.yml"}, "stage": "x"},
             ]}
        ]
    }
    from app.core.preflight import check_topology_assets
    checks = await check_topology_assets(project_dir, None, topology)
    blocks = [c for c in checks if c.result == "block"]
    assert blocks
    assert any("MISSING_ASSET" in (c.code or "") for c in blocks)


@pytest.mark.asyncio
async def test_check_topology_assets_blocks_empty_inline_yaml(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    topology = {
        "nodes": [
            {"id": "n1", "kind": "vm", "role": "admin", "replication": {"scope": "shared"},
             "attachments": [
                 {"source": {"kind": "inline_yaml", "content": "   "}, "stage": "x"},
             ]}
        ]
    }
    from app.core.preflight import check_topology_assets
    checks = await check_topology_assets(project_dir, None, topology)
    assert any(c.result == "block" for c in checks)


@pytest.mark.asyncio
async def test_check_topology_assets_external_git_must_match_registered(tmp_path):
    """external_git URL host must match a registered Source's base_url."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    topology = {
        "nodes": [
            {"id": "n1", "kind": "vm", "role": "admin", "replication": {"scope": "shared"},
             "attachments": [
                 {"source": {"kind": "external_git", "url": "https://attacker.com/evil.git"},
                  "stage": "x"},
             ]}
        ]
    }
    from app.core.preflight import check_topology_assets
    checks = await check_topology_assets(
        project_dir, None, topology,
        registered_source_base_urls={"https://github.com"},
    )
    blocks = [c for c in checks if c.result == "block"]
    assert any("EXTERNAL_GIT_NOT_REGISTERED" in (c.code or "") for c in blocks)


@pytest.mark.asyncio
async def test_check_topology_assets_external_git_passes_when_registered(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    topology = {
        "nodes": [
            {"id": "n1", "kind": "vm", "role": "admin", "replication": {"scope": "shared"},
             "attachments": [
                 {"source": {"kind": "external_git", "url": "https://github.com/me/repo.git"},
                  "stage": "x"},
             ]}
        ]
    }
    from app.core.preflight import check_topology_assets
    checks = await check_topology_assets(
        project_dir, None, topology,
        registered_source_base_urls={"https://github.com"},
    )
    assert all(c.result == "pass" for c in checks)


@pytest.mark.asyncio
async def test_check_topology_assets_catalog_warn_when_no_catalog_dir(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    topology = {
        "nodes": [
            {"id": "n1", "kind": "vm", "role": "admin", "replication": {"scope": "shared"},
             "attachments": [
                 {"source": {"kind": "catalog_role", "ref": "software.install.wazuh"},
                  "stage": "x"},
             ]}
        ]
    }
    from app.core.preflight import check_topology_assets
    checks = await check_topology_assets(project_dir, None, topology)
    # No catalog_dir provided → warn (cannot verify), not block
    assert any(c.result == "warn" and "CATALOG" in (c.code or "") for c in checks)


@pytest.mark.asyncio
async def test_vmid_safety_blocks_protected_vmids():
    """Topology that computes a VMID into the protected range must block."""
    from app.core.preflight import check_vmid_safety_for_topology

    topology = {
        "nodes": [
            {"id": "vm-bad", "kind": "vm", "role": "admin",
             "replication": {"scope": "shared"},
             "template_vmid": 9001, "vmid_base": 100},  # 100 is protected
        ]
    }
    check = await check_vmid_safety_for_topology(topology, team_count=1, host_overrides=None)
    assert check.result == "block"
    assert "protected" in check.detail.lower() or "100" in check.detail


@pytest.mark.asyncio
async def test_vmid_safety_blocks_duplicate_vmids():
    """Two per-team VMs whose computed VMIDs collide must block."""
    from app.core.preflight import check_vmid_safety_for_topology

    # team_count=2, vms_per_team=2:
    # node-a (base=5000) team 1 seq 0 → 5000 + 1*2 + 0 = 5002
    # node-b (base=4999) team 1 seq 1 → 4999 + 1*2 + 1 = 5002  (collides)
    topology = {
        "nodes": [
            {"id": "vm-a", "kind": "vm", "role": "trainee",
             "replication": {"scope": "per_team"},
             "vmid_base": 5000},
            {"id": "vm-b", "kind": "vm", "role": "trainee",
             "replication": {"scope": "per_team"},
             "vmid_base": 4999},
        ]
    }
    check = await check_vmid_safety_for_topology(topology, team_count=2, host_overrides=None)
    assert check.result == "block"
    assert "duplicate" in check.detail.lower()


@pytest.mark.asyncio
async def test_vmid_safety_passes_for_safe_topology():
    """Safe topology with shared and per-team VMs at vmid_base=5000 → pass."""
    from app.core.preflight import check_vmid_safety_for_topology

    topology = {
        "nodes": [
            {"id": "vm-shared", "kind": "vm", "role": "admin",
             "replication": {"scope": "shared"},
             "vmid_base": 5000},
            {"id": "vm-team-a", "kind": "vm", "role": "trainee",
             "replication": {"scope": "per_team"},
             "vmid_base": 5100},
            {"id": "vm-team-b", "kind": "lxc", "role": "trainee",
             "replication": {"scope": "per_team"},
             "vmid_base": 5200},
        ]
    }
    check = await check_vmid_safety_for_topology(topology, team_count=2, host_overrides=None)
    assert check.result == "pass", f"expected pass, got {check.result}: {check.detail}"


def test_check_topology_node_role_blocks_missing_role():
    """A VM node without a 'role' field must produce a block check."""
    from app.core.preflight import check_topology_node_role

    topology = {
        "nodes": [
            {"id": "vm-noop", "kind": "vm", "replication": {"scope": "shared"}},
        ]
    }
    checks = check_topology_node_role(topology)
    blocks = [c for c in checks if c.result == "block"]
    assert blocks, "expected at least one block for missing role"
    assert any(c.code == "TOPOLOGY_NODE_MISSING_ROLE" for c in blocks)


def test_check_topology_node_role_passes_when_all_have_roles():
    """Every VM/LXC node has a role → exactly one pass check."""
    from app.core.preflight import check_topology_node_role

    topology = {
        "nodes": [
            {"id": "vm-a", "kind": "vm", "role": "admin", "replication": {"scope": "shared"}},
            {"id": "lxc-b", "kind": "lxc", "role": "trainee", "replication": {"scope": "per_team"}},
        ]
    }
    checks = check_topology_node_role(topology)
    assert len(checks) == 1
    assert checks[0].result == "pass"


def test_check_topology_node_role_ignores_non_vm_nodes():
    """Networks, routers, etc. without 'role' do not block — only vm/lxc require it."""
    from app.core.preflight import check_topology_node_role

    topology = {
        "nodes": [
            {"id": "net-a", "kind": "network"},
            {"id": "rt-a", "kind": "router"},
            {"id": "vm-a", "kind": "vm", "role": "admin", "replication": {"scope": "shared"}},
        ]
    }
    checks = check_topology_node_role(topology)
    assert all(c.result == "pass" for c in checks)
    assert len(checks) == 1


# ---------------------------------------------------------------------------
# Declarative preflight_checks[] dispatcher (Task B-T10, spec §5.5.4)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_declarative_checks_resolves_known_names():
    """Known name → matching callable runs and returns its checks."""
    from app.core.preflight import run_declarative_checks

    topology = {
        "nodes": [
            {"id": "vm-a", "kind": "vm", "role": "admin",
             "replication": {"scope": "shared"}},
        ]
    }
    out = await run_declarative_checks(
        ["topology.node_role"],
        context={"topology": topology},
    )
    assert len(out) == 1
    assert out[0].result == "pass"
    assert out[0].check == "topology_node_role"


@pytest.mark.asyncio
async def test_run_declarative_checks_warns_on_unknown_name():
    """Unknown name → warn with code UNKNOWN_PREFLIGHT_CHECK, not a raise."""
    from app.core.preflight import run_declarative_checks

    out = await run_declarative_checks(
        ["does.not.exist"],
        context={},
    )
    assert len(out) == 1
    assert out[0].result == "warn"
    assert out[0].code == "UNKNOWN_PREFLIGHT_CHECK"
    assert "does.not.exist" in out[0].detail


@pytest.mark.asyncio
async def test_run_declarative_checks_passes_kwargs_from_context(tmp_path):
    """Dispatcher introspects signature and pulls each kwarg from context."""
    from app.core.preflight import run_declarative_checks

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "ansible").mkdir()
    (project_dir / "ansible" / "configure.yml").write_text("- hosts: all\n")

    topology = {
        "nodes": [
            {"id": "n1", "kind": "vm", "role": "admin",
             "replication": {"scope": "shared"},
             "attachments": [
                 {"source": {"kind": "file_upload",
                             "path": "ansible/configure.yml"},
                  "stage": "configure"},
                 {"source": {"kind": "external_git",
                             "url": "https://github.com/me/repo.git"},
                  "stage": "configure"},
             ]},
        ]
    }
    out = await run_declarative_checks(
        ["topology.assets"],
        context={
            "project_dir": project_dir,
            "catalog_dir": None,
            "topology": topology,
            "registered_source_base_urls": {"https://github.com"},
            # Extra keys in context should be ignored harmlessly.
            "team_count": 99,
            "api_url": "ignored",
        },
    )
    assert all(c.result == "pass" for c in out), [
        (c.result, c.detail) for c in out if c.result != "pass"
    ]


@pytest.mark.asyncio
async def test_run_declarative_checks_swallows_exceptions(monkeypatch):
    """A raising check → warn DECLARATIVE_CHECK_RAISED, not propagation."""
    from app.core import preflight

    def boom(topology):
        raise RuntimeError("boom")

    monkeypatch.setitem(preflight._DECLARATIVE_CHECKS, "topology.node_role", boom)

    out = await preflight.run_declarative_checks(
        ["topology.node_role"],
        context={"topology": {}},
    )
    assert len(out) == 1
    assert out[0].result == "warn"
    assert out[0].code == "DECLARATIVE_CHECK_RAISED"
    assert "RuntimeError" in out[0].detail
    assert "topology.node_role" in out[0].detail
