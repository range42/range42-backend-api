"""A declared SDN plan is checked against the actual target before execution."""
import json

import httpx
import pytest

from app.core.models import ProxmoxHost


def plan():
    return {"mode": "sdn", "zone": "r42lab", "vnets": [
        {"vnet": "r42blue", "subnet": "10.42.70.0/24", "gateway": "10.42.70.1", "snat": True},
    ]}


def scenario(tmp_path, value=None):
    root = tmp_path / "scenario"
    (root / "manifest").mkdir(parents=True)
    if value is not None:
        (root / "manifest/scenario_networks.json").write_text(json.dumps(value))
    return root


def target():
    return ProxmoxHost(id="h", name="lab", node_name="pve01", api_url="https://pve.test:8006",
                       token_ref="user@pve!test=do-not-log")


def responses(*, installed=False):
    return {
        "/access/permissions": {"/sdn": {"SDN.Allocate": 1}},
        "/cluster/sdn/zones": [{"zone": "r42lab", "type": "simple", "state": "unchanged"}] if installed else [],
        "/cluster/sdn/vnets": [{"vnet": "r42blue", "zone": "r42lab", "state": "unchanged"}] if installed else [],
        "/cluster/sdn/vnets/r42blue/subnets": [{"subnet": "r42lab-10.42.70.0-24", "cidr": "10.42.70.0/24",
                                               "gateway": "10.42.70.1", "snat": 1}] if installed else [],
        "/nodes/pve01/network": [{"iface": "r42blue", "type": "bridge", "active": 1}] if installed else [],
        "/nodes/pve01/sdn/zones": [{"zone": "r42lab", "status": "available"}] if installed else [],
        "/nodes/pve01/sdn/zones/r42lab/content": [{"vnet": "r42blue", "status": "available", "statusmsg": None}] if installed else [],
    }


async def check(root, tmp_path, monkeypatch, data=None, *, scope="full", bundle=True):
    from app.core.scenario_networks import check_scenario_networks
    bundle_root = tmp_path / "bundles"
    if bundle:
        entry = bundle_root / "proxmox/sdn_network.bootstrap/main.yml"
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text("- hosts: proxmox\n  tasks: []\n")
    monkeypatch.setenv("RANGE42_BUNDLE_DIR", str(bundle_root))
    calls = []

    def handle(request):
        assert request.method == "GET"
        calls.append(request)
        path = request.url.path.removeprefix("/api2/json")
        value = (data if data is not None else responses()).get(path, [])
        return value if isinstance(value, httpx.Response) else httpx.Response(200, json={"data": value})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await check_scenario_networks(root, target(), scope=scope, client=client)
    return result, calls


@pytest.mark.asyncio
async def test_missing_network_manifest_preserves_hand_authored_scenarios(tmp_path, monkeypatch):
    result, calls = await check(scenario(tmp_path), tmp_path, monkeypatch)
    assert result == []
    assert calls == []


@pytest.mark.asyncio
async def test_new_sdn_network_passes_as_planned_creation(tmp_path, monkeypatch):
    result, calls = await check(scenario(tmp_path, plan()), tmp_path, monkeypatch)
    assert result[-1].result == "pass"
    assert "create" in result[-1].detail.lower()
    assert all(r.headers["Authorization"] == "PVEAPIToken=user@pve!test=do-not-log" for r in calls)


@pytest.mark.asyncio
async def test_missing_sdn_bundle_is_actionable_and_never_falls_back(tmp_path, monkeypatch):
    result, calls = await check(scenario(tmp_path, plan()), tmp_path, monkeypatch, bundle=False)
    assert result[-1].code == "SDN_BUNDLE_UNAVAILABLE"
    assert result[-1].result == "block"
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"snat": "false"}, {"gateway": "192.168.1.1"}, {"subnet": "10.42.70.1/24"},
    {"vnet": "name-too-long"}, {"gateway": "10.42.70.0"},
])
async def test_invalid_network_parameters_block_before_any_api_call(tmp_path, monkeypatch, change):
    value = plan()
    value["vnets"][0].update(change)
    result, calls = await check(scenario(tmp_path, value), tmp_path, monkeypatch)
    assert result[-1].code == "SCENARIO_NETWORK_INVALID"
    assert calls == []


@pytest.mark.asyncio
async def test_sdn_apply_requires_allocate_permission(tmp_path, monkeypatch):
    data = responses()
    data["/access/permissions"] = {"/sdn": {"SDN.Audit": 1}}
    result, _ = await check(scenario(tmp_path, plan()), tmp_path, monkeypatch, data)
    assert result[-1].code == "SDN_PERMISSION_REQUIRED"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"/cluster/sdn/zones": [{"zone": "r42lab", "type": "vxlan"}]},
    {"/cluster/sdn/zones": [{"zone": "r42lab", "type": "simple", "nodes": "othernode"}]},
    {"/cluster/sdn/vnets": [{"vnet": "r42blue", "zone": "unrelated"}]},
    {"/nodes/pve01/network": [{"iface": "r42blue", "type": "bridge", "active": 1}]},
])
async def test_sdn_name_or_host_conflicts_block(tmp_path, monkeypatch, change):
    data = responses()
    data.update(change)
    result, _ = await check(scenario(tmp_path, plan()), tmp_path, monkeypatch, data)
    assert result[-1].code == "SDN_CONFLICT"


@pytest.mark.asyncio
async def test_unrelated_pending_sdn_change_blocks_cluster_apply(tmp_path, monkeypatch):
    data = responses()
    data["/cluster/sdn/vnets"] = [{"vnet": "other", "zone": "other", "state": "new",
                                  "pending": {"zone": "other"}}]
    result, _ = await check(scenario(tmp_path, plan()), tmp_path, monkeypatch, data)
    assert result[-1].code == "SDN_PENDING_CHANGES"


@pytest.mark.asyncio
async def test_configure_needs_active_network_but_not_bootstrap_bundle_or_allocate(tmp_path, monkeypatch):
    data = responses(installed=True)
    data["/access/permissions"] = {"/sdn": {"SDN.Audit": 1}}
    result, calls = await check(scenario(tmp_path, plan()), tmp_path, monkeypatch, data,
                                scope="configure", bundle=False)
    assert result[-1].result == "pass", result
    assert not any("permissions" in str(r.url) for r in calls)


@pytest.mark.asyncio
async def test_sdn_runtime_status_handles_vnets_absent_from_normal_interface_api(tmp_path, monkeypatch):
    # Observed on pve-range42: ip reports the VNet UP, but /node/network
    # lists only normal interfaces. SDN status lives under node/sdn/zones.
    data = responses(installed=True)
    data["/nodes/pve01/network"] = []
    data["/nodes/pve01/sdn/zones"] = [{"zone": "r42lab", "status": "available"}]
    data["/nodes/pve01/sdn/zones/r42lab/content"] = [{"vnet": "r42blue", "status": "available", "statusmsg": None}]
    result, calls = await check(scenario(tmp_path, plan()), tmp_path, monkeypatch, data,
                                scope="configure", bundle=False)
    assert result[-1].result == "pass", result
    assert any(request.url.path.endswith("/sdn/zones/r42lab/content") for request in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime_zone,runtime_vnets", [
    ([], [{"vnet": "r42blue", "status": "available"}]),
    ([{"zone": "r42lab", "status": "error"}], [{"vnet": "r42blue", "status": "available"}]),
    ([{"zone": "r42lab", "status": "available"}], []),
    ([{"zone": "r42lab", "status": "available"}], [{"vnet": "r42blue", "status": "error"}]),
])
async def test_inactive_sdn_runtime_blocks_even_with_an_active_same_named_interface(tmp_path, monkeypatch, runtime_zone, runtime_vnets):
    data = responses(installed=True)
    data["/nodes/pve01/sdn/zones"] = runtime_zone
    data["/nodes/pve01/sdn/zones/r42lab/content"] = runtime_vnets
    result, _ = await check(scenario(tmp_path, plan()), tmp_path, monkeypatch, data,
                            scope="configure", bundle=False)
    assert result[-1].code == "SDN_NOT_READY"


@pytest.mark.asyncio
async def test_sdn_runtime_read_errors_fail_closed_without_response_body(tmp_path, monkeypatch):
    data = responses(installed=True)
    data["/nodes/pve01/sdn/zones"] = httpx.Response(403, text="do-not-log")
    result, _ = await check(scenario(tmp_path, plan()), tmp_path, monkeypatch, data,
                            scope="configure", bundle=False)
    assert result[-1].code == "SCENARIO_NETWORK_UNREADABLE"
    assert "do-not-log" not in repr(result)


@pytest.mark.asyncio
async def test_configure_does_not_offer_to_create_missing_network(tmp_path, monkeypatch):
    result, _ = await check(scenario(tmp_path, plan()), tmp_path, monkeypatch, scope="configure", bundle=False)
    assert result[-1].code == "SDN_NOT_READY"


@pytest.mark.asyncio
async def test_subnet_drift_blocks_without_mutating_shared_network(tmp_path, monkeypatch):
    data = responses(installed=True)
    data["/cluster/sdn/vnets/r42blue/subnets"][0]["snat"] = 0
    result, _ = await check(scenario(tmp_path, plan()), tmp_path, monkeypatch, data)
    assert result[-1].code == "SDN_CONFLICT"


@pytest.mark.asyncio
async def test_network_api_failure_does_not_include_credentials_or_response_body(tmp_path, monkeypatch):
    data = responses()
    data["/cluster/sdn/zones"] = httpx.Response(403, text="do-not-log")
    result, _ = await check(scenario(tmp_path, plan()), tmp_path, monkeypatch, data)
    assert result[-1].code == "SCENARIO_NETWORK_UNREADABLE"
    assert "do-not-log" not in repr(result)


@pytest.mark.asyncio
async def test_existing_bridge_compatibility_requires_an_active_bridge(tmp_path, monkeypatch):
    data = responses()
    data["/nodes/pve01/network"] = [{"iface": "vmbr7", "type": "bridge", "active": 0}]
    result, _ = await check(scenario(tmp_path, {"mode": "existing_bridge", "bridges": ["vmbr7"]}),
                            tmp_path, monkeypatch, data, bundle=False)
    assert result[-1].code == "BRIDGE_NOT_READY"


@pytest.mark.asyncio
async def test_overlapping_declared_subnets_block(tmp_path, monkeypatch):
    value = plan()
    value["vnets"].append({"vnet": "r42red", "subnet": "10.42.70.0/25", "snat": False})
    result, calls = await check(scenario(tmp_path, value), tmp_path, monkeypatch)
    assert result[-1].code == "SCENARIO_NETWORK_INVALID"
    assert calls == []


@pytest.mark.asyncio
async def test_teardown_does_not_require_guest_network_readiness(tmp_path, monkeypatch):
    result, calls = await check(scenario(tmp_path, plan()), tmp_path, monkeypatch,
                                scope="teardown", bundle=False)
    assert result == []
    assert calls == []


@pytest.mark.asyncio
async def test_pending_controller_blocks_global_apply(tmp_path, monkeypatch):
    data = responses()
    data["/cluster/sdn/controllers"] = [{"controller": "unrelated", "state": "changed"}]
    result, _ = await check(scenario(tmp_path, plan()), tmp_path, monkeypatch, data)
    assert result[-1].code == "SDN_PENDING_CHANGES"


@pytest.mark.asyncio
async def test_unrelated_ipv6_subnet_does_not_block_ipv4_deployment(tmp_path, monkeypatch):
    data = responses(installed=True)
    data["/cluster/sdn/vnets/r42blue/subnets"].append({"cidr": "fd42::/64", "subnet": "r42lab-fd42::-64"})
    result, _ = await check(scenario(tmp_path, plan()), tmp_path, monkeypatch, data)
    assert result[-1].result == "pass"


@pytest.mark.asyncio
@pytest.mark.parametrize("address", [
    {"cidr": "10.42.70.2/24"}, {"address": "10.42.70.2", "netmask": "255.255.255.0"},
])
async def test_sdn_subnet_must_not_overlap_management_or_legacy_bridge(tmp_path, monkeypatch, address):
    data = responses()
    data["/nodes/pve01/network"] = [{"iface": "vmbr0", "active": 1, "type": "bridge", **address}]
    result, _ = await check(scenario(tmp_path, plan()), tmp_path, monkeypatch, data)
    assert result[-1].code == "SDN_CONFLICT"


@pytest.mark.asyncio
async def test_matching_vnet_gateway_interface_is_allowed(tmp_path, monkeypatch):
    data = responses(installed=True)
    data["/nodes/pve01/network"][0]["cidr"] = "10.42.70.1/24"
    result, _ = await check(scenario(tmp_path, plan()), tmp_path, monkeypatch, data)
    assert result[-1].result == "pass"


@pytest.mark.asyncio
@pytest.mark.parametrize("feature,endpoint,value", [
    ("prefix-lists", "/cluster/sdn/prefix-lists", [{"id": "other", "state": "deleted"}]),
    ("route-maps", "/cluster/sdn/route-maps/entries", [{"id": "other", "state": "changed"}]),
    ("fabrics", "/cluster/sdn/fabrics/all", {"fabrics": [], "nodes": [{"node_id": "pve02", "state": "new"}]}),
])
async def test_pending_modern_sdn_objects_block_apply_when_server_supports_them(tmp_path, monkeypatch, feature, endpoint, value):
    data = responses()
    data["/cluster/sdn"] = [{"id": feature}]
    data["/access/permissions"]["/nodes"] = {"Sys.Audit": 1}
    data[endpoint] = value
    result, calls = await check(scenario(tmp_path, plan()), tmp_path, monkeypatch, data)
    assert result[-1].code == "SDN_PENDING_CHANGES"
    assert next(request for request in calls if request.url.path.endswith(endpoint)).url.params["pending"] == "1"


@pytest.mark.asyncio
async def test_fabric_node_visibility_is_required_before_global_apply(tmp_path, monkeypatch):
    data = responses()
    data["/cluster/sdn"] = [{"id": "fabrics"}]
    data["/cluster/sdn/fabrics/all"] = {"fabrics": [], "nodes": []}
    result, _ = await check(scenario(tmp_path, plan()), tmp_path, monkeypatch, data)
    assert result[-1].code == "SDN_PERMISSION_REQUIRED"
