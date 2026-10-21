"""An authenticated network reply must prove this exact environment and hold."""

import copy
import importlib
import importlib.util

import pytest

from app.core.errors import Range42Error
from app.core.physical_nodes import digest
from tests.core.test_foundation_capabilities import attestation
from tests.core.test_environment_networks import selected


def service():
    assert importlib.util.find_spec("app.core.environment_network_execution"), (
        "Missing original environment-network lifecycle"
    )
    return importlib.import_module("app.core.environment_network_execution")


def profile():
    from app.core.environment_networks import compile_plan

    plan = compile_plan(selected())
    node = attestation()
    node["node_name"] = plan["node"]
    policy = {
        "version": 1,
        "node_name": plan["node"],
        "installation_id": node["installation_id"],
        "networks": copy.deepcopy(plan["networks"]),
        "management": copy.deepcopy(plan["management"]),
        "uplink": {
            "interface": "vmbr0",
            "address": "10.42.0.2/30",
            "gateway": "10.42.0.1",
        },
        "routing_table": 42550,
        "routing_priority": 4250,
        "protected_public_cidrs": ["31.22.122.64/26"],
    }
    return {
        "protocol": "range42-environment-network-profile-v1",
        "policy": policy,
        "policy_revision": digest(policy),
        "node": node,
        "supported_operations": ["profile", "prepare", "status"],
        "execution_ready": False,
    }


def request():
    from app.core.environment_networks import compile_plan

    value = profile()
    node = value["node"]
    foundation = {
        "protocol": "range42-foundation-request-v1",
        "capacity_request": {
            "request_id": "3" * 32,
            "installation_id": node["installation_id"],
            "root_policy_revision": node["configuration_revision"],
            "commitment": {
                "physical_node_id": node["physical_node_id"],
                "hardware_binding": node["hardware_binding"],
            },
        },
        "plan": {
            "environment_id": selected().id,
            "target_node": selected().target_node,
        },
    }
    return {
        "protocol": "range42-environment-network-request-v1",
        "policy_revision": value["policy_revision"],
        "foundation_request": foundation,
        "plan": compile_plan(selected()),
    }


def receipt():
    original = request()
    node = profile()["node"]
    return {
        "protocol": "range42-environment-network-receipt-v1",
        "request_digest": digest(original),
        "policy_revision": original["policy_revision"],
        "environment_id": original["plan"]["environment_id"],
        "node": node,
        "status": "ready",
        "network_ready": True,
        "execution_ready": False,
        "proof": {
            "request_digest": digest(original),
            "owner": {
                "protocol": "range42-environment-network-owner-v1",
                **{
                    field: node[field]
                    for field in [
                        "physical_node_id",
                        "hardware_binding",
                        "installation_id",
                        "node_name",
                    ]
                },
            },
            "interfaces_digest": "a" * 64,
            "firewall_digest": "b" * 64,
            "routes_digest": "c" * 64,
        },
    }


def test_ready_network_reply_keeps_general_execution_closed():
    result = service().verified_receipt(request(), receipt())
    assert result.network_ready and result.execution_ready is False


@pytest.mark.parametrize(
    "damage",
    [
        "request",
        "policy",
        "environment",
        "node",
        "hardware",
        "owner",
        "proof",
        "ready",
        "integer_ready",
        "execution",
        "unknown",
    ],
)
def test_network_reply_rejects_changed_ownership_and_unsupported_readiness(damage):
    value = receipt()
    if damage in {"request", "policy"}:
        value[damage + "_digest" if damage == "request" else "policy_revision"] = (
            "f" * 64
        )
    if damage == "environment":
        value["environment_id"] = "env-" + "f" * 24
    if damage == "node":
        value["node"]["node_name"] = "foreign"
    if damage == "hardware":
        value["node"]["hardware_binding"] = "f" * 64
    if damage == "owner":
        value["proof"]["owner"]["physical_node_id"] = "f" * 32
    if damage == "proof":
        value["proof"] = None
    if damage == "ready":
        value["network_ready"] = False
    if damage == "integer_ready":
        value["network_ready"] = 1
    if damage == "execution":
        value["execution_ready"] = True
    if damage == "unknown":
        value["command"] = "id"
    with pytest.raises(Range42Error):
        service().verified_receipt(request(), value)


@pytest.mark.parametrize(
    "damage",
    [
        "version",
        "revision",
        "unknown",
        "wide_management",
        "public_transit",
        "wrong_host",
        "earlier_priority",
        "boolean_table",
        "duplicate_network",
    ],
)
def test_root_module_profile_is_bounded_before_core_journals_any_request(damage):
    value = profile()
    if damage == "version":
        value["policy"]["version"] = True
    if damage == "revision":
        value["policy_revision"] = "f" * 64
    if damage == "unknown":
        value["policy"]["command"] = "id"
    if damage == "wide_management":
        value["policy"]["management"]["subnet"] = "10.78.255.0/24"
    if damage == "public_transit":
        value["policy"]["uplink"]["address"] = "8.8.8.2/30"
    if damage == "wrong_host":
        value["policy"]["management"]["host_address"] = "10.78.255.2/29"
    if damage == "earlier_priority":
        value["policy"]["routing_priority"] = 32766
    if damage == "boolean_table":
        value["policy"]["routing_table"] = True
    if damage == "duplicate_network":
        value["policy"]["networks"].append(value["policy"]["networks"][0])
    if damage != "revision":
        value["policy_revision"] = digest(value["policy"])
    with pytest.raises(ValueError):
        service().NetworkProfile.model_validate(value)
