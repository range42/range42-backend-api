"""Native scenarios are discoverable without generating UI project files."""
import json

from app.core.catalog_index import detail_at_path, discover


def native_tree(root, path="scenarios/demo"):
    base = root / path
    (base / "manifest").mkdir(parents=True)
    (base / "templates").mkdir()
    (base / "templates/ansible-inventory.j2").write_text("all: {}\n")
    (base / "main.yml").write_text("- import_playbook: stage_00/main.yml\n")
    (base / "main_vms_only.yml").write_text("- import_playbook: stage_00/main.yml\n")
    (base / "manifest/scenario_vms.json").write_text(json.dumps({
        "scenario": "demo", "version": 2, "description": "A complete SDN lab",
        "vms": [{"vm_id": 2001, "vm_name": "guest", "ip": "10.42.0.10", "bridge": "net42"}],
        "templates": [{"vm_id": 9901}],
    }))
    return base


def test_native_scenario_browse_and_detail_share_identity(tmp_path):
    base = native_tree(tmp_path)
    (base / "manifest/feature_flags.yml").write_text(
        "features:\n  - id: WAZUH\n    label: Monitoring\n    default: false\n")
    entries = discover(tmp_path)
    assert [(item["path"], item["kind"]) for item in entries] == [("scenarios/demo", "scenario")]
    detail = detail_at_path(tmp_path, "scenarios/demo")
    assert detail["name"] == "demo"
    assert detail["description"] == "A complete SDN lab"
    assert detail["document"]["entrypoints"] == {"full": "main.yml", "vms": "main_vms_only.yml"}
    assert detail["document"]["vm_count"] == 1
    assert detail["document"]["template_count"] == 1
    assert detail["document"]["features"][0]["default"] is False
    assert detail["document"]["execution"] == "native_context"


def test_private_repository_can_use_a_different_scenario_root(tmp_path):
    native_tree(tmp_path, "training/exercise-a")
    assert discover(tmp_path)[0]["path"] == "training/exercise-a"
    assert detail_at_path(tmp_path, "training/exercise-a")["kind"] == "scenario"


def test_placeholder_or_malformed_scenario_does_not_become_deployable(tmp_path):
    base = native_tree(tmp_path)
    (base / "manifest/scenario_vms.json").write_text('{"vms": "invalid"}')
    assert discover(tmp_path) == []
    (base / "manifest/scenario_vms.json").unlink()
    assert detail_at_path(tmp_path, "scenarios/demo") is None


def test_native_discovery_rejects_manifest_symlink_outside_source(tmp_path):
    base = native_tree(tmp_path / "repo")
    manifest = base / "manifest/scenario_vms.json"
    outside = tmp_path / "private.json"
    outside.write_text(manifest.read_text())
    manifest.unlink()
    manifest.symlink_to(outside)
    assert discover(tmp_path / "repo") == []


def test_discovery_does_not_publish_arbitrary_feature_values(tmp_path):
    base = native_tree(tmp_path)
    (base / "manifest/feature_flags.yml").write_text(
        "features:\n  - id: SERVICE\n    label: Service\n    default: false\n    token: private\n"
        "  - id: INVALID\n    default: secret-value\n")
    features = detail_at_path(tmp_path, "scenarios/demo")["document"]["features"]
    assert features == [{"id": "SERVICE", "label": "Service", "default": False}]


def test_native_discovery_does_not_advertise_shell_scripts_as_api_actions(tmp_path):
    base = native_tree(tmp_path)
    (base / "demo.delete_all.sh").write_text("#!/bin/sh\nexit 0\n")
    detail = detail_at_path(tmp_path, "scenarios/demo")
    assert "teardown" not in detail["document"]["entrypoints"]


def test_generated_scenario_without_native_inventory_template_is_not_misclassified(tmp_path):
    base = native_tree(tmp_path)
    (base / "templates/ansible-inventory.j2").unlink()
    (base / "hosts.yml").write_text("all: {}\n")
    detail = detail_at_path(tmp_path, "scenarios/demo")
    assert detail is None or detail["kind"] != "scenario"
