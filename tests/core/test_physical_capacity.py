"""Bounded physical facts are observations, never an allocation permission."""

import json
from importlib.util import find_spec

import pytest

from tests.core.test_physical_nodes import proof
from tests.core.test_preparation_transport import Process, config


def services():
    assert find_spec("app.core.physical_capacity") is not None, (
        "Missing bounded physical-capacity observation"
    )
    from app.core import physical_capacity

    return physical_capacity


def capacity():
    return {
        "protocol": "range42-capacity-observation-v1",
        "node": proof(supported_operations=["inspect", "capacity"]),
        "resources": {
            "logical_cpus": 32,
            "memory_total_mib": 65536,
            "max_vcpus": 128,
            "memory_reserve_mib": 16384,
            "guests": [
                {
                    "vm_id": 45000,
                    "kind": "qemu",
                    "state": "stopped",
                    "template": False,
                    "vmgenid": "11111111-2222-4333-8444-555555555555",
                    "resumable": True,
                    "memory_mib": 57344,
                    "vcpus": 8,
                    "configuration_revision": "f" * 64,
                }
            ],
            "storage": [
                {
                    "storage": "ceph-range",
                    "namespace": "",
                    "pool_id": "1" * 64,
                    "cluster_id": "2" * 64,
                    "rados_pool_id": 7,
                    "replicas": 3,
                    "logical_available_bytes": 3000000,
                    "cluster_raw_available_bytes": 9000000,
                    "potential_bytes": 1048576,
                    "volumes": [{"volume": "vm-45000-disk-0", "size_bytes": 1048576}],
                }
            ],
            "execution_ready": False,
            "infrastructure_qualified": False,
        },
    }


def test_observation_preserves_negative_memory_headroom_and_full_stopped_commitments():
    mod = services()
    observed = mod.CapacityObservation.model_validate(capacity())
    summary = mod.summarize(observed)
    assert summary["configured_memory_mib"] == 57344
    assert summary["memory_headroom_mib"] == -8192
    assert summary["configured_vcpus"] == 8 and summary["resumable_guests"] == 1
    assert summary["allocation_granted"] is summary["execution_ready"] is False


@pytest.mark.parametrize(
    "damage",
    [
        "ready",
        "integer_ready",
        "duplicate_guest",
        "template_charge",
        "missing_generation_field",
        "duplicate_volume",
        "wrong_volume_sum",
        "unknown_command",
        "unknown_operation",
    ],
)
def test_malformed_or_optimistic_native_facts_are_rejected(damage):
    mod = services()
    value = capacity()
    resources = value["resources"]
    guest = resources["guests"][0]
    if damage == "ready":
        resources["execution_ready"] = True
    elif damage == "integer_ready":
        resources["execution_ready"] = 0
    elif damage == "duplicate_guest":
        resources["guests"] *= 2
    elif damage == "template_charge":
        guest.update(template=True, resumable=False)
    elif damage == "missing_generation_field":
        guest.pop("vmgenid")
    elif damage == "duplicate_volume":
        resources["storage"][0]["volumes"] *= 2
    elif damage == "wrong_volume_sum":
        resources["storage"][0]["potential_bytes"] = 0
    elif damage == "unknown_command":
        value["command"] = "id"
    elif damage == "unknown_operation":
        value["node"]["supported_operations"] = ["capacity", "inspect", "write"]
    with pytest.raises(ValueError):
        mod.CapacityObservation.model_validate(value)


@pytest.mark.asyncio
async def test_capacity_transport_sends_only_the_fixed_action_and_checks_installation(
    tmp_path, monkeypatch
):
    mod = services()
    from app.core import preparation_transport as transport

    config(tmp_path, monkeypatch)
    process = Process(json.dumps({"ok": True, "result": capacity()}).encode())

    async def spawn(*args, **kwargs):
        assert (
            args[-1] == "range42-preparation-test"
            and "StrictHostKeyChecking=yes" in args
        )
        return process

    monkeypatch.setattr(transport.asyncio, "create_subprocess_exec", spawn)
    observed = await mod.inspect_capacity(
        transport.agent_configuration("first", "pve-test")
    )
    assert observed.resources.guests[0].memory_mib == 57344
    assert json.loads(process.input) == {"action": "capacity"}
    damaged = capacity()
    damaged["node"]["installation_id"] = "9" * 32
    process = Process(json.dumps({"ok": True, "result": damaged}).encode())
    from app.core.errors import Range42Error

    with pytest.raises(Range42Error):
        await mod.inspect_capacity(transport.agent_configuration("first", "pve-test"))
