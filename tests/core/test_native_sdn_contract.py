"""Exercise the application's adapter against the unchanged native release."""
import os
from pathlib import Path

import pytest

from app.core.errors import Range42Error


@pytest.fixture(params=["synthetic", "pinned"])
def native_profile(monkeypatch, tmp_path, request):
    from app.core import native_sdn
    if request.param == "pinned":
        root = os.getenv("RANGE42_NATIVE_CONTRACT_FIXTURE")
        if not root:
            pytest.skip("set RANGE42_NATIVE_CONTRACT_FIXTURE to the pinned native source export")
        root = Path(root)
    else:
        root = tmp_path / "reviewed"
        bundles = root / "playbooks/bundles"
        controller = root / "controller/roles/range42-ansible_roles-proxmox_controller"
        for directory in (bundles, controller):
            directory.mkdir(parents=True)
            (directory / "main.yml").write_text("---\n")
        monkeypatch.setattr(native_sdn, "REVIEWED_TREES", {
            (native_sdn.tree_digest(bundles), native_sdn.tree_digest(controller)): native_sdn.NATIVE_CONTRACT,
        })
    from app.core import runtime_operations
    profile = {"environment": {
        "RANGE42_BUNDLE_DIR": str(root / "playbooks/bundles"),
        "ANSIBLE_ROLES_PATH": str(root / "controller/roles"),
    }, "components": {}}
    monkeypatch.setattr(runtime_operations, "runtime_snapshot", lambda: (profile, "f" * 64))
    return profile


@pytest.mark.parametrize("kind", ["vm_firewall", "scenario_firewall", "sdn_snat"])
def test_pinned_native_runtime_is_recognized_without_private_marker_files(native_profile, kind):
    from app.core.runtime_operations import operation_profile
    result = operation_profile(kind)
    assert result["contract"] == "native-sdn-20260921"
    assert kind in result["operations"]
    assert result["fingerprint"] == "f" * 64


def test_shadow_controller_cannot_borrow_later_native_capabilities(native_profile, tmp_path):
    from app.core.runtime_operations import operation_profile
    (tmp_path / "range42-ansible_roles-proxmox_controller").mkdir()
    native_profile["environment"]["ANSIBLE_ROLES_PATH"] = str(tmp_path) + os.pathsep + native_profile["environment"]["ANSIBLE_ROLES_PATH"]
    with pytest.raises(Range42Error, match="matching runtime"):
        operation_profile("sdn_snat")


def test_native_nat_result_uses_independent_nat_rule_observation():
    from app.core.runtime_completion import assess_runtime_result
    from tests.core.test_runtime_operations import state
    current = state()
    current["networks"][0]["configured_snat"] = True
    events = [{"payload": {"res": {"r42_native_snat_observation": {"complete": True, "node": "pve01", "rules": [{
        "action": "network_list_snat_rules", "proxmox_node": "pve01", "snat_host": "r42-proxmox-cli",
        "snat_source": "10.42.70.0/24", "snat_target": "SNAT", "snat_out_iface": "vmbr0", "snat_count": 1,
    }]}}}}]
    result = assess_runtime_result({"kind": "sdn_snat", "enabled": True, "vnet": "r42blue"},
        {"subnet": "10.42.70.0/24", "contract": "native-sdn-20260921"}, current, events)
    assert result["desired_reached"] is True
    assert result["live_snat_rule_count"] == 1
    assert result["live_forwarding_verified"] is False


@pytest.mark.parametrize("change", ["incomplete", "wrong_node", "wrong_rule_node", "negative", "non_nat", "pre_apply"])
def test_native_readback_refuses_incomplete_or_unrelated_observations(change):
    from app.core.runtime_completion import assess_runtime_result
    from tests.core.test_runtime_operations import state
    current = state()
    current["networks"][0]["configured_snat"] = True
    row = {"proxmox_node": "pve01", "snat_host": "r42-proxmox-cli", "snat_source": "10.42.70.0/24",
           "snat_target": "SNAT", "snat_out_iface": "vmbr0", "snat_count": 1}
    observation = {"complete": True, "node": "pve01", "rules": [row]}
    if change == "incomplete":
        observation["complete"] = False
    if change == "wrong_node":
        observation["node"] = "pve02"
    if change == "wrong_rule_node":
        row["proxmox_node"] = "pve02"
    if change == "negative":
        row["snat_count"] = -1
    if change == "non_nat":
        row["snat_target"] = "LOG"
    response = {"r42_native_snat_observation": observation}
    if change == "pre_apply":
        response = {"network_list_snat_rules": [row]}
    result = assess_runtime_result({"kind": "sdn_snat", "enabled": True, "vnet": "r42blue"},
        {"subnet": "10.42.70.0/24", "contract": "native-sdn-20260921"}, current, [{"payload": {"res": response}}])
    assert result["desired_reached"] is False
    assert result["live_snat_rule_count"] is None
