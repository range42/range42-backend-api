import httpx
import pytest

from tests.core.test_runtime_state import responses, write_scenario
from tests.core.test_scenario_networks import target


def lifecycle_data():
    data = responses()
    data["/access/permissions"] = {"/": {"Sys.Audit": 1, "VM.Audit": 1, "SDN.Allocate": 1}}
    data["/nodes/pve01/network"] = []
    data["/nodes"] = [{"node": "pve01"}]
    data["/cluster/sdn/vnets"][0]["alias"] = "range42-deployment-dep"
    data["/cluster/resources"] = []
    return data


async def read(tmp_path, data):
    from app.core import runtime_networks
    write_scenario(tmp_path)
    def handle(request):
        assert request.method == "GET"
        path = request.url.path.removeprefix("/api2/json")
        value = data.get(path, httpx.Response(403))
        return value if isinstance(value, httpx.Response) else httpx.Response(200, json={"data": value})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        return await runtime_networks.read_network_lifecycle(tmp_path, target(), deployment_id="dep", client=client)


@pytest.mark.asyncio
async def test_delete_requires_exact_marker_and_preserves_shared_zone(tmp_path):
    from app.core import runtime_networks
    observation = await read(tmp_path, lifecycle_data())
    result = runtime_networks.network_plan({"kind": "sdn_network", "action": "delete", "vnet": "r42blue"}, observation)
    assert result["network"]["zone"] == "r42lab"
    assert result["preserve_zone"] is True
    assert [step["bundle"] for step in result["steps"]] == [
        "proxmox/sdn_network.delete.sdn_subnet", "proxmox/sdn_network.delete.sdn_vnet", "proxmox/sdn_network.apply",
        "proxmox/sdn_network.reconcile.snat_rules",
    ]
    assert result["steps"][0]["variables"]["BUNDLE_SDN_SUBNET_ID"] == "r42blue-10.42.70.0-24"
    assert result["attachments"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("conflict", ["foreign_marker", "template", "foreign_lxc", "unreadable_guest", "hidden_inventory", "extra_subnet", "pending", "mismatched_gateway"])
async def test_network_delete_refuses_ambiguous_or_attached_networks(tmp_path, conflict):
    from app.core import runtime_networks
    from app.core.errors import Range42Error
    data = lifecycle_data()
    if conflict == "foreign_marker":
        data["/cluster/sdn/vnets"][0]["alias"] = "range42-deployment-other"
    elif conflict == "hidden_inventory":
        data["/access/permissions"] = {"/": {"SDN.Allocate": 1}}
    elif conflict == "extra_subnet":
        data["/cluster/sdn/vnets/r42blue/subnets"].append({"subnet": "extra", "cidr": "10.43.0.0/24"})
    elif conflict == "pending":
        data["/cluster/sdn/controllers"] = [{"controller": "shared", "state": "new"}]
    elif conflict == "mismatched_gateway":
        data["/cluster/sdn/vnets/r42blue/subnets"][0]["gateway"] = "10.42.70.2"
    else:
        kind = "lxc" if conflict == "foreign_lxc" else "qemu"
        data["/cluster/resources"] = [{"vmid": 9001, "node": "othernode", "type": kind, "template": int(conflict == "template")}]
        if conflict != "unreadable_guest":
            data[f"/nodes/othernode/{kind}/9001/config"] = {"net0": "virtio=aa,bridge=r42blue"}
    observation = await read(tmp_path, data)
    with pytest.raises(Range42Error):
        runtime_networks.network_plan({"kind": "sdn_network", "action": "delete", "vnet": "r42blue"}, observation)


@pytest.mark.asyncio
async def test_missing_declared_network_can_be_created_and_marked_without_replacing_zone(tmp_path):
    from app.core import runtime_networks
    data = lifecycle_data()
    data["/cluster/sdn/vnets"] = []
    observation = await read(tmp_path, data)
    result = runtime_networks.network_plan({"kind": "sdn_network", "action": "create", "vnet": "r42blue"}, observation)
    assert result["steps"][0]["bundle"] == "proxmox/sdn_network.create.sdn_vnet"
    assert result["steps"][0]["variables"]["sdn_vnet_alias"] == "range42-deployment-dep"
    assert all("sdn_zone" not in step["bundle"] for step in result["steps"])


@pytest.mark.asyncio
async def test_legacy_bridge_migration_is_an_explicit_blocker(tmp_path):
    from app.core import runtime_networks
    from app.core.errors import Range42Error
    data = lifecycle_data()
    data["/cluster/sdn/vnets"] = []
    data["/nodes/pve01/network"] = [{"iface": "r42blue", "type": "bridge"}]
    observation = await read(tmp_path, data)
    with pytest.raises(Range42Error, match="bridge|interface"):
        runtime_networks.network_plan({"kind": "sdn_network", "action": "create", "vnet": "r42blue"}, observation)


@pytest.mark.asyncio
async def test_legacy_interface_subnets_are_included_in_zero_nat_preservation(tmp_path):
    data = lifecycle_data()
    data["/nodes/pve01/network"] = [{"iface": "vmbr90", "address": "10.90.0.1", "netmask": "255.255.255.0"}]
    observation = await read(tmp_path, data)
    assert observation["available"] is True
    assert "10.90.0.0/24" in observation["all_subnets"]


@pytest.mark.asyncio
async def test_cluster_apply_requires_nat_readback_on_every_affected_node(tmp_path):
    from app.core import runtime_networks
    from app.core.errors import Range42Error
    data = lifecycle_data()
    data["/nodes"].append({"node": "pve02"})
    observation = await read(tmp_path, data)
    with pytest.raises(Range42Error, match="single-node"):
        runtime_networks.network_plan({"kind": "sdn_network", "action": "delete", "vnet": "r42blue"}, observation)


def test_network_operation_schema_requires_shared_ack_and_accepts_no_arbitrary_subnet():
    from pydantic import TypeAdapter, ValidationError
    from app.schemas.v1.runtime import RuntimeOperation
    body = {"kind": "sdn_network", "action": "delete", "vnet": "r42blue", "acknowledge_shared_scope": True}
    assert TypeAdapter(RuntimeOperation).validate_python(body).vnet == "r42blue"
    for patch in ({"acknowledge_shared_scope": False}, {"subnet": "10.1.0.0/24"}, {"action": "apply_all"}):
        with pytest.raises(ValidationError):
            TypeAdapter(RuntimeOperation).validate_python({**body, **patch})


@pytest.mark.parametrize("removed,count,expected", [(True, 0, True), (False, 0, False), (True, None, False), (True, 1, False)])
def test_delete_result_requires_confirmed_absence_and_no_live_source_nat(removed, count, expected):
    from app.core.runtime_completion import assess_runtime_result
    from tests.core.test_runtime_operations import state
    from tests.core.test_runtime_observe import sample
    current = state()
    current["network_lifecycle"] = {"available": True, "networks": [{"vnet": "r42blue", "exists": not removed}]}
    current["networks"][0]["active"] = not removed
    observed = sample()
    observed["rules"][0]["snat_count"] = count
    events = [{"payload": {"res": {"r42_native_snat_observation": observed}}}]
    plan = {"contract": "native-sdn-20260921", "network": {"subnet": "10.42.70.0/24"}}
    events.insert(0, {"payload": {"res": {"r42_lifecycle_nat_before": {"10.42.70.0/24": 1, "10.90.0.0/24": 2}}}})
    result = assess_runtime_result({"kind": "sdn_network", "action": "delete", "vnet": "r42blue"}, plan, current, events)
    assert result["desired_reached"] is expected
    assert result["live_forwarding_verified"] is False


def test_lifecycle_does_not_succeed_when_an_unrelated_nat_count_changed():
    from app.core.runtime_completion import assess_runtime_result
    from tests.core.test_runtime_operations import state
    from tests.core.test_runtime_observe import sample
    current = state()
    current["network_lifecycle"] = {"available": True, "networks": [{"vnet": "r42blue", "exists": False}]}
    current["networks"][0]["active"] = False
    observed = sample()
    observed["rules"][0]["snat_count"] = 0
    events = [{"payload": {"res": {"r42_lifecycle_nat_before": {"10.90.0.0/24": 3}}}},
              {"payload": {"res": {"r42_native_snat_observation": observed}}}]
    result = assess_runtime_result({"kind": "sdn_network", "action": "delete", "vnet": "r42blue"},
        {"contract": "native-sdn-20260921", "network": {"subnet": "10.42.70.0/24"}}, current, events)
    assert result["desired_reached"] is False
    assert result["unrelated_nat_preserved"] is False
    assert result["partial"] is True
