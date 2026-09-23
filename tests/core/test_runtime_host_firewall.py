import pytest

from app.core.errors import Range42Error
from tests.core.test_runtime_operations import state


def request(enabled=True):
    return {"kind": "host_firewall", "enabled": enabled, "acknowledge_shared_scope": True}


@pytest.mark.parametrize("enabled", [True, False])
def test_host_firewall_uses_native_anti_lockout_composite_without_guest_targets(enabled):
    from app.core.runtime_operations import plan_operation
    observed = state()
    observed["firewall"] = {"datacenter_enabled": False, "node_enabled": False, "errors": []}
    plan = plan_operation(request(enabled), observed)
    assert plan["vmids"] == []
    assert plan["bundle"] == f"firewall/in_proxmox/firewall.{'enable' if enabled else 'disable'}.datacenter_and_nodes"
    assert plan["shared_scope"] == "datacenter_and_selected_node"
    assert plan["before"] == {"datacenter_enabled": False, "node_enabled": False}


@pytest.mark.parametrize("value", [None, "unknown"])
def test_host_firewall_requires_readable_switches(value):
    from app.core.runtime_operations import plan_operation
    observed = state()
    observed["firewall"] = {"datacenter_enabled": value, "node_enabled": False, "errors": []}
    with pytest.raises(Range42Error, match="switch"):
        plan_operation(request(), observed)


@pytest.mark.parametrize("dc,node,complete,partial", [(True, True, True, False), (True, False, False, True), (None, None, False, False)])
def test_host_readback_never_turns_one_switch_or_unknown_into_success(dc, node, complete, partial):
    from app.core.runtime_completion import assess_runtime_result
    observed = state()
    observed["firewall"] = {"datacenter_enabled": dc, "node_enabled": node, "errors": []}
    result = assess_runtime_result(request(), {"vmids": [], "missing_vmids": []}, observed, [])
    assert result["desired_reached"] is complete
    assert result["partial"] is partial
    assert result["live_forwarding_verified"] is False
