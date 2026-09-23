import pytest

from tests.core.test_runtime_operations import state


def sample(node="pve01"):
    return {"complete": True, "node": node, "rules": [{"snat_source": "10.42.70.0/24", "snat_out_iface": "vmbr0",
        "snat_target": "MASQUERADE", "snat_count": 1, "snat_host": "r42-proxmox-cli", "proxmox_node": node},
        {"snat_source": "10.90.0.0/24", "snat_out_iface": "vmbr0", "snat_target": "MASQUERADE", "snat_count": 2,
         "snat_host": "r42-proxmox-cli", "proxmox_node": node}]}


def test_native_observation_plans_no_guest_or_network_mutation():
    from app.core.runtime_operations import plan_operation
    plan = plan_operation({"kind": "runtime_observe"}, state())
    assert plan["vmids"] == []
    assert plan["bundle"] == "firewall/in_proxmox/firewall.report.status"
    assert plan["read_only"] is True


@pytest.mark.parametrize("change", [None, "wrong_node", "missing", "malformed", "later_invalid"])
def test_native_readback_filters_other_networks_and_preserves_unknown(change):
    from app.core.runtime_completion import assess_runtime_result
    observed = sample("other" if change == "wrong_node" else "pve01")
    if change == "malformed":
        observed["rules"][0]["snat_count"] = -1
    events = [] if change == "missing" else [{"payload": {"res": {"r42_native_snat_observation": observed}}}]
    if change == "later_invalid":
        events.append({"payload": {"res": {"r42_native_snat_observation": {"complete": False}}}})
    result = assess_runtime_result({"kind": "runtime_observe"}, {"contract": "native-sdn-20260921"}, state(), events)
    assert result["desired_reached"] is (change is None)
    assert result["live_nat"]["available"] is (change is None)
    assert result["live_forwarding_verified"] is False
    if change is None:
        assert result["live_nat"]["rules"] == sample()["rules"][:1]
    else:
        assert result["live_nat"]["rules"] == []
