"""A runtime plan cannot expand beyond verified deployment resources."""
import copy
import json

import pytest

from app.core.errors import Range42Error


def state():
    return {"vms": [{"vm_id": 3191, "name": "owned-guest", "status": "owned",
                     "firewall_enabled": False, "nics": [{"index": 0, "firewall_enabled": False}]}],
            "sdn": {"pending_changes": False, "errors": []},
            "networks": [{"vnet": "r42blue", "identity_matches": True, "active": True,
                          "subnet": "10.42.70.0/24", "subnet_id": "r42blue-10.42.70.0-24",
                          "configured_snat": False}]}


@pytest.mark.parametrize("enabled", [False, True])
def test_single_guest_uses_the_composite_bundle_and_all_cards(enabled):
    from app.core.runtime_operations import plan_operation
    result = plan_operation({"kind": "vm_firewall", "vm_id": 3191, "enabled": enabled}, state())
    assert result["bundle"] == f"firewall/in_proxmox/firewall.{'enable' if enabled else 'disable'}.vm"
    assert result["variables"] == {"BUNDLE_VM_ID": 3191}
    assert result["vmids"] == [3191]


@pytest.mark.parametrize("change", ["foreign", "unavailable", "missing", "undeclared"])
def test_single_guest_refuses_unverified_identity(change):
    from app.core.runtime_operations import plan_operation
    current = state()
    current["vms"][0]["status"] = change
    if change == "undeclared":
        current["vms"][0]["vm_id"] = 3192
    with pytest.raises(Range42Error):
        plan_operation({"kind": "vm_firewall", "vm_id": 3191, "enabled": True}, current)


def test_scenario_sweep_records_confirmed_absent_guests_but_refuses_foreign_guests():
    from app.core.runtime_operations import plan_operation
    current = state()
    current["vms"].append({"vm_id": 3192, "status": "missing"})
    result = plan_operation({"kind": "scenario_firewall", "enabled": True}, current)
    assert result["vmids"] == [3191]
    assert result["missing_vmids"] == [3192]
    assert result["bundle"] == "firewall/in_proxmox/firewall.enable.vms"
    current["vms"][1]["status"] = "conflict"
    with pytest.raises(Range42Error):
        plan_operation({"kind": "scenario_firewall", "enabled": True}, current)


@pytest.mark.parametrize("enabled", [False, True])
def test_nat_uses_the_live_id_for_declared_network_and_explicit_desired_state(enabled):
    from app.core.runtime_operations import plan_operation
    result = plan_operation({"kind": "sdn_snat", "vnet": "r42blue", "enabled": enabled,
                             "acknowledge_shared_scope": True}, state())
    assert result["bundle"] == f"proxmox/sdn_network.internet_{'on' if enabled else 'off'}"
    assert result["variables"] == {"BUNDLE_SDN_SUBNET_ID": "r42blue-10.42.70.0-24"}


@pytest.mark.parametrize("change", ["pending", "unknown_pending", "identity", "inactive", "undeclared"])
def test_nat_refuses_pending_or_mismatched_shared_state(change):
    from app.core.runtime_operations import plan_operation
    current = copy.deepcopy(state())
    if change == "pending":
        current["sdn"]["pending_changes"] = True
    elif change == "unknown_pending":
        current["sdn"]["pending_changes"] = None
    elif change == "identity":
        current["networks"][0]["identity_matches"] = False
    elif change == "inactive":
        current["networks"][0]["active"] = False
    else:
        current["networks"][0]["vnet"] = "foreign"
    with pytest.raises(Range42Error):
        plan_operation({"kind": "sdn_snat", "vnet": "r42blue", "enabled": True,
                        "acknowledge_shared_scope": True}, current)


@pytest.mark.parametrize("capability", [None, False, True])
def test_nat_runtime_requires_reviewed_all_subnet_reconciliation(tmp_path, monkeypatch, capability):
    from app.core import runtime_operations
    monkeypatch.setattr(runtime_operations, "runtime_snapshot", lambda: (
        {"environment": {"RANGE42_BUNDLE_DIR": str(tmp_path)}, "components": {}}, "f" * 64,
    ))
    if capability is not None:
        (tmp_path / "runtime-capabilities.json").write_text(json.dumps({
            "version": 1, "operations": ["vm_firewall", "scenario_firewall", "sdn_snat"],
            "snat_reconciles_all_declared_subnets": capability,
        }))
    if capability is True:
        assert runtime_operations.operation_profile("sdn_snat")["fingerprint"] == "f" * 64
    else:
        with pytest.raises(Range42Error) as error:
            runtime_operations.operation_profile("sdn_snat")
        assert error.value.code == "RUNTIME_CAPABILITY_MISSING"


def test_status_advertises_only_installed_capabilities(tmp_path, monkeypatch):
    from app.core import runtime_operations
    monkeypatch.setattr(runtime_operations, "runtime_snapshot", lambda: (
        {"environment": {"RANGE42_BUNDLE_DIR": str(tmp_path)}, "components": {}}, "f" * 64,
    ))
    (tmp_path / "runtime-capabilities.json").write_text(json.dumps({"version": 1, "operations": ["vm_firewall"]}))
    assert runtime_operations.operation_profile("vm_firewall")["operations"] == ["vm_firewall"]
