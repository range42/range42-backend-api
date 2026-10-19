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


def test_native_detail_includes_declared_topology_without_guessing_networks(tmp_path):
    base = native_tree(tmp_path)
    (base / "00_sdn_bootstrap").mkdir()
    (base / "00_sdn_bootstrap/_main.yml").write_text(
        '- hosts: proxmox\n  tasks:\n    - set_fact:\n        _sdn_vnets:\n'
        '          - {vnet: net42, subnet: 10.42.0.0/24, gateway: 10.42.0.1}\n')
    topology = detail_at_path(tmp_path, "scenarios/demo")["document"].get("topology")
    assert topology is not None
    assert topology["vms"][0]["vm_id"] == 2001
    assert topology["templates"] == [{"vm_id": 9901}]
    assert topology["networks"] == [{"vnet": "net42", "subnet": "10.42.0.0/24", "gateway": "10.42.0.1"}]
    assert topology["reservations"]["status"] == "missing"
    (base / "00_sdn_bootstrap/_main.yml").unlink()
    assert detail_at_path(tmp_path, "scenarios/demo")["document"]["topology"]["networks"] == [{"vnet": "net42"}]


def registry(root):
    rows = []
    for path in sorted((root / "scenarios").glob("*/manifest/scenario_vms.json")):
        manifest = json.loads(path.read_text())
        rows.extend({**row, "scenario": path.parents[1].name} for row in manifest["vms"])
        rows.extend({**row, "scenario": path.parents[1].name, "role": "template"} for row in manifest["templates"])
    (root / "scenarios/_reserved.json").write_text("\n".join(json.dumps(row) for row in rows) + "\n")


def test_native_reservations_detect_collisions_but_allow_shared_templates_and_networks(tmp_path):
    native_tree(tmp_path)
    other = native_tree(tmp_path, "scenarios/other")
    manifest = json.loads((other / "manifest/scenario_vms.json").read_text())
    manifest["vms"][0].update(vm_id=2002, ip="10.42.0.11")
    (other / "manifest/scenario_vms.json").write_text(json.dumps(manifest))
    registry(tmp_path)
    def status():
        return detail_at_path(tmp_path, "scenarios/demo")["document"].get("topology", {}).get("reservations")
    assert status() == {"status": "checked", "issues": []}
    manifest["vms"][0]["vm_id"] = 2001
    (other / "manifest/scenario_vms.json").write_text(json.dumps(manifest))
    registry(tmp_path)
    assert status()["status"] == "conflict"
    assert any("2001" in issue and "other" in issue for issue in status()["issues"])


def test_native_reservations_reject_stale_or_malformed_registry(tmp_path):
    native_tree(tmp_path)
    registry(tmp_path)
    ledger = tmp_path / "scenarios/_reserved.json"
    ledger.write_text(ledger.read_text().replace('2001', '2002'))
    result = detail_at_path(tmp_path, "scenarios/demo")["document"].get("topology", {}).get("reservations")
    assert result and result["status"] == "conflict"
    ledger.write_text('not json')
    assert detail_at_path(tmp_path, "scenarios/demo")["document"]["topology"]["reservations"]["status"] == "invalid"


def test_duplicate_vm_rows_inside_one_scenario_are_conflicts(tmp_path):
    base = native_tree(tmp_path)
    path = base / "manifest/scenario_vms.json"
    manifest = json.loads(path.read_text())
    manifest["vms"].append(manifest["vms"][0])
    path.write_text(json.dumps(manifest))
    registry(tmp_path)
    assert detail_at_path(tmp_path, "scenarios/demo")["document"]["topology"]["reservations"]["status"] == "conflict"


def test_reservations_check_normalized_secondary_addresses(tmp_path):
    base = native_tree(tmp_path)
    other = native_tree(tmp_path, "scenarios/other")
    path = other / "manifest/scenario_vms.json"
    manifest = json.loads(path.read_text())
    manifest["vms"][0].update(vm_id=2002, ip="10.42.0.11", nics=[{"bridge": "net42", "ip": "10.42.0.10/24"}])
    path.write_text(json.dumps(manifest))
    registry(tmp_path)
    result = detail_at_path(tmp_path, "scenarios/demo")["document"]["topology"]["reservations"]
    assert result["status"] == "conflict"
    assert any("10.42.0.10" in issue and "other" in issue for issue in result["issues"])
    # Check secondary-to-secondary collisions too, while allowing other networks.
    mine = base / "manifest/scenario_vms.json"
    doc = json.loads(mine.read_text())
    doc["vms"][0].update(ip="10.42.0.12", nics=[{"bridge": "net42", "ip": "10.42.0.10/32"}])
    mine.write_text(json.dumps(doc))
    registry(tmp_path)
    assert detail_at_path(tmp_path, "scenarios/demo")["document"]["topology"]["reservations"]["status"] == "conflict"
    doc["vms"][0]["nics"][0]["bridge"] = "net43"
    mine.write_text(json.dumps(doc))
    registry(tmp_path)
    assert detail_at_path(tmp_path, "scenarios/demo")["document"]["topology"]["reservations"]["status"] == "checked"


def test_shared_templates_require_consistent_secondary_addresses(tmp_path):
    native_tree(tmp_path)
    other = native_tree(tmp_path, "scenarios/other")
    path = other / "manifest/scenario_vms.json"
    doc = json.loads(path.read_text())
    doc["vms"][0].update(vm_id=2002, ip="10.42.0.11")
    doc["templates"][0]["nics"] = [{"bridge": "net43", "ip": "10.43.0.10"}]
    path.write_text(json.dumps(doc))
    registry(tmp_path)
    result = detail_at_path(tmp_path, "scenarios/demo")["document"]["topology"]["reservations"]
    assert result["status"] == "conflict"
    assert any("9901" in issue for issue in result["issues"])
