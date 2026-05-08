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
