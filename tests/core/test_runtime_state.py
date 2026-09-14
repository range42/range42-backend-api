"""Runtime status reads only declared, owned guests and reports unknown states."""
import json

import httpx
import pytest

from tests.core.test_scenario_networks import plan, target


def write_scenario(tmp_path):
    (tmp_path / "manifest").mkdir(exist_ok=True)
    (tmp_path / "manifest/scenario_vms.json").write_text(json.dumps({
        "version": 2, "vms": [{"vm_id": 3191, "vm_name": "owned-guest"}],
    }))
    (tmp_path / "manifest/scenario_networks.json").write_text(json.dumps(plan()))


def responses():
    return {
        "/cluster/firewall/options": {"enable": 1},
        "/nodes/pve01/firewall/options": {"enable": 0},
        "/cluster/resources": [{"vmid": 3191, "name": "owned-guest", "node": "pve01", "type": "qemu"}],
        "/nodes/pve01/qemu/3191/config": {
            "name": "owned-guest", "description": "range42-deployment:dep",
            "net0": "virtio=AA:BB:CC:DD:EE:01,bridge=r42blue,firewall=1",
            "net1": "virtio=AA:BB:CC:DD:EE:02,bridge=r42red",
        },
        "/nodes/pve01/qemu/3191/firewall/options": {"enable": 1},
        "/cluster/nextid": "3191",
        "/cluster/sdn/zones": [{"zone": "r42lab", "type": "simple"}],
        "/cluster/sdn/vnets": [{"vnet": "r42blue", "zone": "r42lab"}],
        "/cluster/sdn/vnets/r42blue/subnets": [{
            "subnet": "r42blue-10.42.70.0-24", "cidr": "10.42.70.0/24", "gateway": "10.42.70.1", "snat": 1,
        }],
        "/cluster/sdn/controllers": [],
        "/cluster/sdn": [],
        "/nodes/pve01/sdn/zones": [{"zone": "r42lab", "status": "available"}],
        "/nodes/pve01/sdn/zones/r42lab/content": [{"vnet": "r42blue", "status": "available"}],
    }


async def read_state(tmp_path, data):
    from app.core.runtime_state import read_runtime_state

    write_scenario(tmp_path)
    requests = []

    def handle(request):
        assert request.method == "GET"
        path = request.url.path.removeprefix("/api2/json")
        requests.append(path)
        value = data.get(path, httpx.Response(404))
        return value if isinstance(value, httpx.Response) else httpx.Response(200, json={"data": value})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await read_runtime_state(tmp_path, target(), deployment_id="dep", client=client)
    return result, requests


@pytest.mark.asyncio
async def test_status_keeps_all_firewall_prerequisites_and_nat_declaration_separate(tmp_path):
    result, _ = await read_state(tmp_path, responses())
    assert result["firewall"] == {"datacenter_enabled": True, "node_enabled": False, "errors": []}
    vm = result["vms"][0]
    assert vm["status"] == "owned"
    assert vm["firewall_enabled"] is True
    assert vm["nics"] == [
        {"index": 0, "bridge": "r42blue", "firewall_enabled": True},
        {"index": 1, "bridge": "r42red", "firewall_enabled": False},
    ]
    assert vm["filtering_configured"] is False
    network = result["networks"][0]
    assert network["identity_matches"] is True
    assert network["configured_snat"] is True
    assert network["active"] is True
    assert network["live_forwarding_verified"] is False
    assert result["sdn"]["pending_changes"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("description", ["", "range42-deployment:other", "range42-deployment:dep-extra"])
async def test_foreign_vm_is_reported_without_reading_its_firewall(tmp_path, description):
    data = responses()
    data["/nodes/pve01/qemu/3191/config"]["description"] = description
    result, requests = await read_state(tmp_path, data)
    assert result["vms"][0]["status"] == "conflict"
    assert "/nodes/pve01/qemu/3191/firewall/options" not in requests


@pytest.mark.asyncio
@pytest.mark.parametrize("hidden", [False, True])
async def test_missing_guests_are_confirmed_globally_before_reporting_absence(tmp_path, hidden):
    data = responses()
    data["/cluster/resources"] = []
    if hidden:
        data["/cluster/nextid"] = httpx.Response(400)
    result, requests = await read_state(tmp_path, data)
    assert "/cluster/nextid" in requests
    assert result["vms"][0]["status"] == ("unavailable" if hidden else "missing")


@pytest.mark.asyncio
async def test_failed_firewall_read_is_unknown_and_does_not_hide_other_status(tmp_path):
    data = responses()
    data["/cluster/firewall/options"] = httpx.Response(403)
    result, _ = await read_state(tmp_path, data)
    assert result["firewall"]["datacenter_enabled"] is None
    assert result["firewall"]["errors"]
    assert result["vms"][0]["filtering_configured"] is None
    assert result["networks"][0]["active"] is True


@pytest.mark.asyncio
async def test_unrelated_pending_sdn_objects_block_shared_apply(tmp_path):
    data = responses()
    data["/cluster/sdn/controllers"] = [{"controller": "unrelated", "state": "new"}]
    result, _ = await read_state(tmp_path, data)
    assert result["sdn"]["pending_changes"] is True
    assert result["sdn"]["errors"]


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [{"name": "reassigned"}, {"template": 1}, {"template": "1"}, {"name": None}])
async def test_fresh_config_identity_overrides_stale_cluster_resource_identity(tmp_path, changed):
    data = responses()
    data["/nodes/pve01/qemu/3191/config"].update(changed)
    result, requests = await read_state(tmp_path, data)
    assert result["vms"][0]["status"] == "conflict"
    assert "/nodes/pve01/qemu/3191/firewall/options" not in requests
