"""The same allocator reserves a gateway VM's address without waiving occupancy."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError


def payload(**changes):
    return {
        "project_key": "gateway-draft",
        "vmid_start": 45000,
        "vmid_end": 45039,
        "networks": [
            {
                "network_id": "participants",
                "bridge": "vmbr600",
                "subnet": "10.78.0.0/28",
                "gateway": "10.78.0.1",
                "gateway_node_id": "router",
            }
        ],
        "vms": [
            {
                "node_id": "guest",
                "vm_id": 45000,
                "nics": [{"index": 0, "network_id": "participants"}],
            },
            {
                "node_id": "router",
                "vm_id": 45010,
                "nics": [{"index": 0, "network_id": "participants", "ip": "10.78.0.1"}],
            },
        ],
        **changes,
    }


def allocate(value, *, occupancy=None, rows=None):
    from app.core.allocation_occupancy import Occupancy
    from app.core.allocation_reservations import _plan
    from app.schemas.v1.allocation import AllocationRequest

    host = SimpleNamespace(protected_vmids_override_json=None)
    return _plan(
        AllocationRequest.model_validate(value),
        host,
        rows or [],
        None,
        occupancy or Occupancy(),
    )


def test_gateway_owner_reserves_its_gateway_while_other_guests_keep_auto_allocation():
    planned = allocate(payload())
    assert planned[0]["nics"][0]["ip"] == "10.78.0.2"
    assert planned[1]["nics"][0]["ip"] == "10.78.0.1"
    swapped = payload()
    swapped["vms"].reverse()
    assert {vm["node_id"]: vm["nics"] for vm in allocate(swapped)} == {
        vm["node_id"]: vm["nics"] for vm in planned
    }


@pytest.mark.parametrize(
    "conflict", ["observed", "lease", "reserved-ip", "other-guest"]
)
def test_gateway_owner_does_not_override_native_occupancy_or_another_claim(conflict):
    from app.core.allocation_occupancy import Occupancy
    from app.core.errors import Range42Error

    value = payload()
    observed = Occupancy()
    rows = []
    if conflict == "observed":
        observed.addresses.add(("vmbr600", "10.78.0.1"))
    elif conflict == "lease":
        rows = [
            SimpleNamespace(
                assignments=[
                    {
                        "vm_id": 45020,
                        "nics": [{"index": 0, "bridge": "vmbr600", "ip": "10.78.0.1"}],
                    }
                ]
            )
        ]
    elif conflict == "reserved-ip":
        value["networks"][0]["reserved_ips"] = ["10.78.0.1"]
    else:
        value["vms"][0]["nics"][0]["ip"] = "10.78.0.1"
    with pytest.raises(Range42Error) as error:
        allocate(value, occupancy=observed, rows=rows)
    assert error.value.code == "ALLOCATION_OCCUPIED"


@pytest.mark.parametrize(
    "fault",
    ["unknown-owner", "missing-ip", "wrong-ip", "missing-gateway", "ambiguous-nic"],
)
def test_gateway_ownership_must_name_one_explicit_matching_nic(fault):
    from app.schemas.v1.allocation import AllocationRequest

    value = payload()
    if fault == "unknown-owner":
        value["networks"][0]["gateway_node_id"] = "absent"
    elif fault == "missing-ip":
        value["vms"][1]["nics"][0].pop("ip")
    elif fault == "wrong-ip":
        value["vms"][1]["nics"][0]["ip"] = "10.78.0.3"
    elif fault == "missing-gateway":
        value["networks"][0].pop("gateway")
    else:
        value["vms"][1]["nics"].append(
            {"index": 1, "network_id": "participants", "ip": "10.78.0.1"}
        )
    with pytest.raises(ValidationError):
        AllocationRequest.model_validate(value)


def test_undeclared_gateway_owner_keeps_the_existing_reserved_address_behavior():
    from app.core.errors import Range42Error

    value = payload()
    value["networks"][0].pop("gateway_node_id")
    with pytest.raises(Range42Error) as error:
        allocate(value)
    assert error.value.code == "ALLOCATION_OCCUPIED"
