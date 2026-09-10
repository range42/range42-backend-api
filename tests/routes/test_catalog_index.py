"""Catalog discovery follows source content and keeps browse/detail aligned."""
import json

import pytest

from app.routes.v1.catalog.entries import _detail_at_path, _discover


def write(root, path, content):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


def test_bundle_without_descriptor_is_indexed_from_all_play_targets(tmp_path):
    path = "bundles/admin/software.install.demo"
    write(tmp_path, f"{path}/main.yml", "# hosts: unexpected\n- hosts: proxmox\n  tasks: []\n- hosts: '{{ global_vm_ssh_name }}'\n  tasks: []\n")
    entries = _discover(tmp_path)
    assert len(entries) == 1
    assert entries[0]["kind"] == "bundle"
    detail = _detail_at_path(tmp_path, path)
    assert detail["document"]["bundle"] == "admin/software.install.demo"
    assert detail["document"]["entrypoint"] == f"{path}/main.yml"
    assert detail["document"]["bundle_kind"] == "XTIER"
    assert detail["document"]["params"] == []


def test_bundle_parameters_enrich_but_cannot_override_derived_identity(tmp_path):
    path = "bundles/generic/system.configure.demo"
    write(tmp_path, f"{path}/main.yml", "- hosts: '{{ TARGET_GROUP }}'\n  tasks: []\n")
    write(tmp_path, f"{path}/bundle_parameters.json", json.dumps({
        "bundle": "../../other", "tier": "wrong", "description": "Configure a demo",
        "params": [{"name": "ENABLED", "type": "bool", "default": False}],
    }))
    detail = _detail_at_path(tmp_path, path)
    assert detail["document"]["bundle"] == "generic/system.configure.demo"
    assert detail["document"]["bundle_kind"] == "GROUP"
    assert detail["document"]["params"][0]["default"] is False


def test_discovery_synthesizes_actual_role_and_container_assets(tmp_path):
    role = "02_ansible_layer/trainee/roles/blue_env/example"
    container = "03_container_layer/docker/admin/new-service"
    write(tmp_path, f"{role}/tasks/main.yaml", "- name: Check service\n  ansible.builtin.debug:\n    msg: ready\n")
    write(tmp_path, f"{container}/compose.yml", "services:\n  web:\n    image: nginx\n")
    write(tmp_path, f"{container}/Dockerfile", "FROM nginx\n")
    write(tmp_path, "03_container_layer/lxc/place_holder", "")
    entries = _discover(tmp_path)
    assert [(entry["path"], entry["kind"]) for entry in entries] == [(role, "ansible_role"), (container, "container")]
    for entry in entries:
        assert _detail_at_path(tmp_path, entry["path"])["kind"] == entry["kind"]


def test_explicit_metadata_wins_without_duplicate_entries(tmp_path):
    write(tmp_path, "role/range42.yaml", "kind: ansible_role\nname: Preferred\n")
    write(tmp_path, "role/meta/main.yml", "galaxy_info:\n  description: Duplicate\n")
    write(tmp_path, "role/tasks/main.yml", "[]\n")
    entries = _discover(tmp_path)
    assert len(entries) == 1
    assert entries[0]["name"] == "Preferred"


def test_invalid_metadata_does_not_break_other_entries(tmp_path):
    write(tmp_path, "bad/range42.yaml", "[not, a, mapping]\n")
    write(tmp_path, "bad2/meta.json", '{"x_range42":{"catalog":"invalid"}}')
    write(tmp_path, "good/range42.yaml", "kind: lab\nname: Good\n")
    assert any(entry["name"] == "Good" for entry in _discover(tmp_path))


def test_catalog_never_reads_outside_checkout(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    write(tmp_path, "private/range42.yaml", "kind: lab\nname: Private\n")
    (root / "leak").symlink_to(tmp_path / "private", target_is_directory=True)
    assert _detail_at_path(root, "../private") is None
    assert _detail_at_path(root, "leak") is None
    assert _discover(root) == []


@pytest.mark.parametrize("hosts,kind", [("proxmox", "INFRA"), ("{{ global_vm_ssh_name }}", "VM"), ("{{ target_group }}", "GROUP"), ("{{ unknown_variable }}", "UNKNOWN")])
def test_bundle_kinds_are_inferred_conservatively(tmp_path, hosts, kind):
    path = "bundles/generic/system.configure.demo"
    write(tmp_path, f"{path}/main.yml", f"- hosts: '{hosts}'\n  tasks: []\n")
    assert _detail_at_path(tmp_path, path)["document"]["bundle_kind"] == kind


def test_gamification_manifests_are_browsable_without_role_metadata(tmp_path):
    path = "04_gamification_layer/web/shared/skins"
    write(tmp_path, f"{path}/manifest.json", json.dumps({"skins": [{"name": "classic", "label": "Classic"}]}))
    entries = _discover(tmp_path)
    assert entries[0]["kind"] == "gamification"
    assert _detail_at_path(tmp_path, path)["document"]["skins"][0]["name"] == "classic"


def test_malformed_bundle_descriptor_does_not_hide_valid_bundle(tmp_path):
    path = "bundles/generic/system.configure.demo"
    write(tmp_path, f"{path}/main.yml", "- hosts: '{{ TARGET_GROUP }}'\n  tasks: []\n")
    write(tmp_path, f"{path}/bundle_parameters.json", "{broken")
    assert _detail_at_path(tmp_path, path)["document"]["bundle_kind"] == "GROUP"


def test_role_internal_compose_files_are_not_separate_catalog_components(tmp_path):
    path = "02_ansible_layer/admin/roles/software.configure.example"
    write(tmp_path, f"{path}/tasks/main.yml", "[]\n")
    write(tmp_path, f"{path}/files/compose.yml", "services:\n  demo:\n    image: nginx\n")
    entries = _discover(tmp_path)
    assert [entry["path"] for entry in entries] == [path]


def test_bundle_names_outside_naming_grammar_are_flagged(tmp_path):
    path = "bundles/generic/my-random-thing"
    write(tmp_path, f"{path}/main.yml", "- hosts: '{{ TARGET_GROUP }}'\n  tasks: []\n")
    detail = _detail_at_path(tmp_path, path)
    assert detail["document"]["grammar_valid"] is False
