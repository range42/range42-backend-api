"""Reports separate configured chains, switch interpretation and live evidence."""
import httpx
import pytest

from tests.core.test_runtime_state import responses, write_scenario
from tests.core.test_scenario_networks import target


async def report(tmp_path, data):
    from app.core import runtime_state
    write_scenario(tmp_path)
    requests = []

    def handle(request):
        assert request.method == "GET"
        path = request.url.path.removeprefix("/api2/json")
        requests.append(path)
        value = data.get(path, httpx.Response(403))
        return value if isinstance(value, httpx.Response) else httpx.Response(200, json={"data": value})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await runtime_state.read_runtime_report(tmp_path, target(), deployment_id="dep", client=client)
    return result, requests


def data_with_chains():
    data = responses()
    for base in ("/cluster", "/nodes/pve01", "/nodes/pve01/qemu/3191"):
        data[base + "/firewall/rules"] = [
            {"pos": 0, "type": "in", "action": "ACCEPT", "proto": "tcp", "dport": "22", "enable": 1,
             "digest": "private-internal", "unexpected": "never expose"},
        ]
        data[base + "/firewall/aliases"] = [{"name": "admins", "cidr": "10.2.3.4/32", "comment": "SSH"}]
    return data


@pytest.mark.asyncio
async def test_reports_preserve_chain_order_and_observation_boundaries(tmp_path):
    result, _ = await report(tmp_path, data_with_chains())
    assert result["version"] == 1
    assert result["observed_at"]
    assert result["target_host_id"] == target().id
    assert result["traffic_verified"] is False
    assert result["live_nat"]["available"] is False
    assert [chain["scope"] for chain in result["chains"]] == ["datacenter", "node", "vm"]
    assert all(chain["available"] for chain in result["chains"])
    rule = result["chains"][0]["rules"][0]
    assert rule["position"] == 0 and rule["enabled"] is True and rule["destination_port"] == "22"
    assert "digest" not in str(result) and "never expose" not in str(result)
    assert result["chains"][2]["aliases"][0]["name"] == "admins"
    assert result["cards"][0]["filtering_configured"] is True
    assert result["cards"][1]["filtering_configured"] is False
    assert "nic_disabled" in result["cards"][1]["reasons"]
    assert result["switches"]["datacenter_enabled"] is True
    assert result["networks"][0]["vnet"] == "r42blue"
    assert result["networks"][0]["identity_matches"] is True
    assert result["sdn"]["pending_changes"] is False


@pytest.mark.asyncio
async def test_foreign_guests_are_never_used_to_read_rules_or_aliases(tmp_path):
    data = data_with_chains()
    data["/nodes/pve01/qemu/3191/config"]["description"] = "range42-deployment:foreign"
    result, requested = await report(tmp_path, data)
    assert not any(path.endswith(("/rules", "/aliases")) and "/qemu/" in path for path in requested)
    assert not result["cards"]
    assert all(chain["scope"] != "vm" for chain in result["chains"])


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [httpx.Response(403), [{"pos": 0, "action": "ACCEPT"}], [{"pos": 0, "type": "in", "action": "ACCEPT", "enable": "unknown"}]])
async def test_unreadable_or_malformed_chain_stays_unknown_without_hiding_other_chains(tmp_path, bad):
    data = data_with_chains()
    data["/cluster/firewall/rules"] = bad
    result, _ = await report(tmp_path, data)
    assert result["chains"][0]["available"] is False
    assert result["chains"][0]["rules"] == []
    assert result["chains"][1]["available"] is True
    assert result["partial"] is True


@pytest.mark.asyncio
async def test_missing_dc_observation_does_not_override_a_disabled_card(tmp_path):
    data = data_with_chains()
    data["/cluster/firewall/options"] = httpx.Response(403)
    result, _ = await report(tmp_path, data)
    assert result["cards"][0]["filtering_configured"] is None
    assert result["cards"][1]["filtering_configured"] is False


@pytest.mark.asyncio
async def test_unreadable_networks_are_unknown_in_the_typed_report(tmp_path):
    data = data_with_chains()
    data["/cluster/sdn/vnets"] = httpx.Response(403)
    result, _ = await report(tmp_path, data)
    assert result["partial"] is True
    assert result["networks"][0]["active"] is None
    assert result["networks"][0]["identity_matches"] is None
    assert result["sdn"]["errors"]
