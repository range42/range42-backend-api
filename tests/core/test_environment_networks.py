"""Core derives environment bridge ownership from the registered profile only."""

import copy
import importlib
import importlib.util

import pytest

from tests.core.test_workshop_foundations import context
from app.core.workshop_profiles import WorkshopProfile
from app.core.errors import Range42Error


def service():
    assert importlib.util.find_spec("app.core.environment_networks"), (
        "Missing canonical environment network plan"
    )
    return importlib.import_module("app.core.environment_networks")


def selected():
    value = context(purpose="workshop")
    profile = value.workshop_profile.model_dump()
    profile["managed_gateway"] = {
        "management_bridge": "vmbr550",
        "management_subnet": "10.78.255.0/29",
    }
    profile["networks"] = [
        {
            "bridge": f"vmbr{600 + index}",
            "subnet": f"10.78.0.{index * 16}/28",
            "gateway": f"10.78.0.{index * 16 + 1}",
        }
        for index in range(16)
    ]
    value.workshop_profile = WorkshopProfile.model_validate(profile)
    return value


def test_environment_network_plan_keeps_pool_ownership_separate_from_run_guests():
    value = selected()
    before = copy.deepcopy(value.workshop_profile.model_dump())
    plan = service().compile_plan(value)
    assert plan["owner"] == "range42-environment:" + value.id
    assert plan["environment_id"] == value.id and plan["node"] == "node-b"
    assert [network["bridge"] for network in plan["networks"]] == [
        f"vmbr{600 + i}" for i in range(16)
    ]
    assert plan["management"] == {
        "bridge": "vmbr550",
        "subnet": "10.78.255.0/29",
        "host_address": "10.78.255.1/29",
    }
    assert not {"vm_id", "guests", "uplink", "commands", "paths"} & plan.keys()
    assert value.workshop_profile.model_dump() == before


@pytest.mark.parametrize(
    "damage",
    [
        "static_context",
        "registration",
        "no_profile",
        "no_gateway",
        "target",
    ],
)
def test_environment_preparation_requires_its_registered_bounded_target(damage):
    value = selected()
    if damage == "static_context":
        value.id = "lab-demo"
    if damage == "foreign_purpose":
        value.purpose = "native"
    if damage == "registration":
        value.registration_issue = "The original target changed"
    if damage == "no_profile":
        value.workshop_profile = None
    if damage == "no_gateway":
        value.workshop_profile = value.workshop_profile.model_copy(
            update={"managed_gateway": None}
        )
    if damage == "target":
        value.target_node = ""
    with pytest.raises(Range42Error):
        service().compile_plan(value)
