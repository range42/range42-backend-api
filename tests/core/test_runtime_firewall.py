import httpx
import pytest

from tests.core.test_runtime_state import responses, write_scenario
from tests.core.test_scenario_networks import target


def data():
    rows = responses()
    rows["/nodes/pve01/qemu/3191/firewall/aliases"] = [{"name": "labclients", "cidr": "10.42.70.0/24", "comment": "range42-deployment:dep", "digest": "a" * 40}]
    rows["/cluster/firewall/aliases"] = []
    rows["/nodes/pve01/qemu/3191/firewall/rules"] = [
        {"pos": 0, "type": "in", "action": "ACCEPT", "proto": "tcp", "dport": "22", "enable": 1, "comment": "management", "digest": "b" * 40},
        {"pos": 1, "type": "in", "action": "ACCEPT", "proto": "tcp", "dport": "443", "enable": 1, "comment": "range42-deployment:dep;rule:web", "digest": "b" * 40},
    ]
    rows["/nodes/pve01/qemu/3191/firewall/ipset"] = []
    return rows


async def preview(tmp_path, rows, request):
    from app.core import runtime_firewall
    write_scenario(tmp_path)
    def handle(req):
        assert req.method == "GET"
        value = rows.get(req.url.path.removeprefix("/api2/json"), httpx.Response(403))
        return value if isinstance(value, httpx.Response) else httpx.Response(200, json={"data": value})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        return await runtime_firewall.firewall_plan(tmp_path, target(), deployment_id="dep", request=request, client=client)


@pytest.mark.asyncio
async def test_alias_rename_uses_atomic_native_api_and_preserves_cidr(tmp_path):
    result = await preview(tmp_path, data(), {"kind": "firewall_alias", "scope": "vm", "vm_id": 3191,
        "action": "rename", "name": "labclients", "new_name": "students", "acknowledge_shared_scope": True})
    assert result["api_change"]["method"] == "PUT"
    assert result["api_change"]["body"] == {"rename": "students", "cidr": "10.42.70.0/24", "comment": "range42-deployment:dep", "digest": "a" * 40}
    assert result["vmids"] == [3191]
    assert result["expected_aliases"] == [{"name": "students", "cidr": "10.42.70.0/24", "comment": "range42-deployment:dep"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["referenced", "ipset_reference", "foreign", "foreign_vm", "name_conflict", "no_digest"])
async def test_alias_refuses_unsafe_rename_or_unverifiable_ownership(tmp_path, case):
    from app.core.errors import Range42Error
    rows = data()
    if case == "referenced":
        rows["/nodes/pve01/qemu/3191/firewall/rules"][1]["source"] = "labclients"
    elif case == "ipset_reference":
        rows["/nodes/pve01/qemu/3191/firewall/ipset"] = [{"name": "people"}]
        rows["/nodes/pve01/qemu/3191/firewall/ipset/people"] = [{"cidr": "labclients"}]
    elif case == "foreign":
        rows["/nodes/pve01/qemu/3191/firewall/aliases"][0]["comment"] = "somebody else"
    elif case == "foreign_vm":
        rows["/nodes/pve01/qemu/3191/config"]["description"] = "range42-deployment:other"
    elif case == "name_conflict":
        rows["/nodes/pve01/qemu/3191/firewall/aliases"].append({"name": "students", "cidr": "10.90.0.0/24"})
    else:
        rows["/nodes/pve01/qemu/3191/firewall/aliases"][0].pop("digest")
    with pytest.raises(Range42Error):
        await preview(tmp_path, rows, {"kind": "firewall_alias", "scope": "vm", "vm_id": 3191,
            "action": "rename", "name": "labclients", "new_name": "students", "acknowledge_shared_scope": True})


@pytest.mark.asyncio
async def test_rule_move_preserves_management_and_uses_native_digest(tmp_path):
    result = await preview(tmp_path, data(), {"kind": "firewall_rule", "scope": "vm", "vm_id": 3191,
        "action": "move", "position": 1, "move_to": 0, "acknowledge_shared_scope": True})
    assert result["api_change"]["method"] == "PUT"
    assert result["api_change"]["body"] == {"moveto": 0, "digest": "b" * 40}
    assert [row["destination_port"] for row in result["expected_rules"]] == ["443", "22"]


@pytest.mark.asyncio
async def test_outbound_allow_rule_cannot_be_removed_as_it_may_carry_management_replies(tmp_path):
    from app.core.errors import Range42Error
    rows = data()
    rows["/nodes/pve01/qemu/3191/firewall/rules"][1].update(type="out", dport="1024:65535")
    with pytest.raises(Range42Error, match="management"):
        await preview(tmp_path, rows, {"kind": "firewall_rule", "scope": "vm", "vm_id": 3191,
            "action": "delete", "position": 1, "acknowledge_shared_scope": True})


@pytest.mark.asyncio
async def test_moving_down_translates_final_position_to_native_insertion_point(tmp_path):
    rows = data()
    rows["/nodes/pve01/qemu/3191/firewall/rules"][0].update(dport="80", comment="range42-deployment:dep;rule:http")
    result = await preview(tmp_path, rows, {"kind": "firewall_rule", "scope": "vm", "vm_id": 3191,
        "action": "move", "position": 0, "move_to": 1, "acknowledge_shared_scope": True})
    assert result["api_change"]["body"]["moveto"] == 2
    assert [row["destination_port"] for row in result["expected_rules"]] == ["443", "80"]


@pytest.mark.asyncio
@pytest.mark.parametrize("rule", [
    {"direction": "in", "action": "DROP", "protocol": "tcp", "destination_port": "22"},
    {"direction": "in", "action": "DROP", "protocol": "tcp", "destination_port": "1:100"},
    {"direction": "out", "action": "DROP", "protocol": "tcp", "destination_port": "443"},
])
async def test_policy_that_can_block_management_is_refused_before_mutation(tmp_path, rule):
    from app.core.errors import Range42Error
    with pytest.raises(Range42Error, match="management"):
        await preview(tmp_path, data(), {"kind": "firewall_rule", "scope": "vm", "vm_id": 3191,
            "action": "create", "name": "blocked", "rule": {**rule, "enabled": True}, "acknowledge_shared_scope": True})


@pytest.mark.asyncio
async def test_alias_references_must_resolve_in_the_selected_scope(tmp_path):
    from app.core.errors import Range42Error
    with pytest.raises(Range42Error, match="alias"):
        await preview(tmp_path, data(), {"kind": "firewall_rule", "scope": "vm", "vm_id": 3191,
            "action": "create", "name": "web", "rule": {"direction": "in", "action": "ACCEPT", "protocol": "tcp",
            "destination_port": "443", "source": "missing_alias", "enabled": True}, "acknowledge_shared_scope": True})


@pytest.mark.parametrize("match", [True, False, None])
def test_firewall_readback_compares_the_full_ordered_chain_and_aliases(match):
    from app.core.runtime_completion import assess_runtime_result
    from tests.core.test_runtime_operations import state
    observed = state()
    observed["firewall_control"] = {"available": match is not None, "rules": [] if match else [{"action": "DROP"}], "aliases": []}
    plan = {"expected_rules": [], "expected_aliases": []}
    result = assess_runtime_result({"kind": "firewall_rule", "action": "delete"}, plan, observed, [])
    assert result["desired_reached"] is (match is True)
    assert result["live_forwarding_verified"] is False


@pytest.mark.asyncio
async def test_datacenter_alias_cannot_be_deleted_when_used_by_unrelated_guest(tmp_path):
    from app.core.errors import Range42Error
    rows = data()
    rows.update({"/cluster/firewall/aliases": rows["/nodes/pve01/qemu/3191/firewall/aliases"],
                 "/cluster/firewall/rules": [], "/cluster/firewall/ipset": [], "/cluster/firewall/groups": [],
                 "/access/permissions": {"/": {"Sys.Audit": 1, "VM.Audit": 1}}, "/nodes": [{"node": "pve01"}],
                 "/nodes/pve01/firewall/rules": [],
                 "/cluster/resources": [{"vmid": 8888, "type": "lxc", "node": "pve01"}],
                 "/nodes/pve01/lxc/8888/firewall/rules": [{"source": "dc/labclients"}],
                 "/nodes/pve01/lxc/8888/firewall/ipset": []})
    with pytest.raises(Range42Error) as error:
        await preview(tmp_path, rows, {"kind": "firewall_alias", "scope": "datacenter", "action": "delete",
                                     "name": "labclients", "acknowledge_shared_scope": True})
    assert error.value.code == "FIREWALL_ALIAS_REFERENCED"


@pytest.mark.asyncio
async def test_complete_rule_replacement_clears_old_optional_fields_and_keeps_its_name(tmp_path):
    rows = data()
    rows["/nodes/pve01/qemu/3191/firewall/rules"][1]["source"] = "10.42.70.0/24"
    result = await preview(tmp_path, rows, {"kind": "firewall_rule", "scope": "vm", "vm_id": 3191,
        "action": "update", "position": 1, "rule": {"direction": "in", "action": "ACCEPT", "protocol": "tcp",
        "destination_port": "80", "enabled": False}, "acknowledge_shared_scope": True})
    assert "source" in result["api_change"]["body"]["delete"].split(",")
    assert result["expected_rules"][1]["enabled"] is False
    assert result["expected_rules"][1]["source"] is None
    assert result["expected_rules"][1]["comment"] == "range42-deployment:dep;rule:web"


@pytest.mark.asyncio
async def test_cluster_metrics_do_not_invalidate_an_unchanged_alias_review(tmp_path):
    rows = data()
    rows.update({"/cluster/firewall/aliases": rows["/nodes/pve01/qemu/3191/firewall/aliases"],
                 "/cluster/firewall/rules": [], "/cluster/firewall/ipset": [], "/cluster/firewall/groups": [],
                 "/access/permissions": {"/": {"Sys.Audit": 1, "VM.Audit": 1}}, "/nodes": [{"node": "pve01", "cpu": 0.1}],
                 "/nodes/pve01/firewall/rules": [], "/cluster/resources": []})
    request = {"kind": "firewall_alias", "scope": "datacenter", "action": "delete", "name": "labclients", "acknowledge_shared_scope": True}
    first = await preview(tmp_path, rows, request)
    rows["/nodes"][0]["cpu"] = 0.2
    assert await preview(tmp_path, rows, request) == first
