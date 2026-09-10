import json

import pytest

from app.core import project
from app.core.errors import Range42Error


def scenario_tree(root, *, subdir="", vmids=None):
    scenario = root / subdir / "scenarios" / "content"
    (scenario / "manifest").mkdir(parents=True)
    (scenario / "main.yml").write_text("- import_playbook: configure.yml\n")
    (scenario / "configure.yml").write_text("- hosts: guest\n  tasks: []\n")
    (scenario / "hosts.yml").write_text("all:\n  hosts:\n    guest:\n      ansible_connection: local\n")
    (scenario / "manifest/scenario_vms.json").write_text(json.dumps({
        "scenario": "content", "version": 1,
        "vms": [{"vm_id": vmid} for vmid in (vmids or [])],
    }))
    return scenario


def resolve(root, **kwargs):
    resolver = getattr(project, "resolve_project_scenario", None)
    assert callable(resolver), "Pinned project scenarios need a shared file resolver"
    return resolver(root, scenario_label="content", **kwargs)


def test_concrete_scenario_resolves_shared_repository_subdir(tmp_path):
    scenario = scenario_tree(tmp_path, subdir="projects/lab", vmids=[5000, 5001])
    result = resolve(tmp_path, subdir="projects/lab")
    assert result.project_root == tmp_path / "projects/lab"
    assert result.playbook == scenario / "main.yml"
    assert result.inventory == scenario / "hosts.yml"
    assert result.vmids == [5000, 5001]


@pytest.mark.parametrize("name", ["main.yml", "hosts.yml", "manifest/scenario_vms.json"])
def test_concrete_scenario_requires_its_own_files(tmp_path, name):
    scenario = scenario_tree(tmp_path)
    (scenario / name).unlink()
    with pytest.raises(Range42Error, match=name):
        resolve(tmp_path)


@pytest.mark.parametrize("subdir", ["../outside", "/tmp", "projects/../../outside"])
def test_concrete_scenario_rejects_project_path_traversal(tmp_path, subdir):
    with pytest.raises(Range42Error, match="subdir"):
        resolve(tmp_path, subdir=subdir)


def test_concrete_scenario_rejects_symlink_to_outside_checkout(tmp_path):
    root = tmp_path / "project"
    scenario = scenario_tree(root)
    outside = tmp_path / "outside.yml"
    outside.write_text("- hosts: all\n")
    (scenario / "main.yml").unlink()
    (scenario / "main.yml").symlink_to(outside)
    with pytest.raises(Range42Error, match="outside"):
        resolve(root)


@pytest.mark.parametrize("manifest", [{}, {"vms": "wrong"}, {"vms": [{}]}, {"vms": [{"vm_id": True}]}])
def test_concrete_scenario_rejects_invalid_manifest(tmp_path, manifest):
    scenario = scenario_tree(tmp_path)
    (scenario / "manifest/scenario_vms.json").write_text(json.dumps(manifest))
    with pytest.raises(Range42Error, match="manifest"):
        resolve(tmp_path)


def test_concrete_scenario_rejects_invalid_inventory(tmp_path):
    scenario = scenario_tree(tmp_path)
    (scenario / "hosts.yml").write_text("all: [unterminated")
    with pytest.raises(Range42Error, match="hosts.yml"):
        resolve(tmp_path)
