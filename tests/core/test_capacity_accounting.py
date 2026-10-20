"""Admission uses full durable commitments, not live memory samples or VM names."""

from importlib.util import find_spec

import pytest

from app.core.physical_capacity import CapacityObservation
from tests.core.test_physical_capacity import capacity

GIB = 2**30


def services():
    assert find_spec("app.core.capacity_accounting") is not None, (
        "Missing conservative canonical capacity accounting"
    )
    from app.core import capacity_accounting

    return capacity_accounting


def observation():
    value = capacity()
    value["resources"]["guests"][0]["memory_mib"] = 16384
    value["resources"]["storage"][0].update(
        logical_available_bytes=300 * GIB,
        cluster_raw_available_bytes=900 * GIB,
        potential_bytes=64 * GIB,
        volumes=[{"volume": "vm-45000-disk-0", "size_bytes": 64 * GIB}],
    )
    return CapacityObservation.model_validate(value)


def hold(name="first", vm_id=46000, memory=24576, disk=100 * GIB, **changes):
    return {
        "id": name,
        "allocation_id": "allocation-" + name,
        "deployment_id": "deployment-" + name,
        "physical_node_id": "a" * 32,
        "hardware_binding": "b" * 64,
        "phase": "held",
        "guests": [
            {
                "vm_id": vm_id,
                "memory_mib": memory,
                "vcpus": 6,
                "storage": [
                    {
                        "pool_id": "1" * 64,
                        "cluster_id": "2" * 64,
                        "namespace": "",
                        "replicas": 3,
                        "logical_bytes": disk,
                    }
                ],
                "materialization": None,
            }
        ],
        **changes,
    }


def test_stopped_ordinary_guest_and_nonexpiring_holds_keep_full_commitments():
    mod = services()
    observed = observation()
    first = mod.CapacityCommitment.model_validate(hold())
    second = mod.CapacityCommitment.model_validate(hold("second", vm_id=46001))
    accepted = mod.assess(observed, [], first)
    refused = mod.assess(observed, [first], second)
    assert accepted["admissible"] is True and accepted["memory_headroom_mib"] == 8192
    assert refused["admissible"] is False and refused["memory_headroom_mib"] == -16384
    assert "MEMORY_COMMITTED" in refused["blocking_codes"]
    assert refused["allocation_granted"] is False


def test_aliases_namespaces_and_replication_cannot_multiply_shared_storage_capacity():
    mod = services()
    value = observation().model_dump()
    original = value["resources"]["storage"][0]
    value["resources"]["storage"].append({**original, "storage": "same-pool-alias"})
    observed = CapacityObservation.model_validate(value)
    result = mod.assess(
        observed, [], mod.CapacityCommitment.model_validate(hold(memory=1024))
    )
    assert result["pools"]["1" * 64]["potential_bytes"] == 64 * GIB
    assert result["pools"]["1" * 64]["logical_headroom_bytes"] == 136 * GIB
    assert result["clusters"]["2" * 64]["raw_headroom_bytes"] == 408 * GIB


def test_distinct_pools_on_one_ceph_cluster_share_raw_budget():
    mod = services()
    value = observation().model_dump()
    original = value["resources"]["storage"][0]
    value["resources"]["storage"].append(
        {
            **original,
            "storage": "other-pool",
            "pool_id": "3" * 64,
            "rados_pool_id": 8,
            "volumes": [],
            "potential_bytes": 0,
        }
    )
    existing = hold(memory=1024, disk=150 * GIB)
    candidate = hold("second", vm_id=46001, memory=1024, disk=150 * GIB)
    candidate["guests"][0]["storage"][0]["pool_id"] = "3" * 64
    result = mod.assess(
        CapacityObservation.model_validate(value),
        [mod.CapacityCommitment.model_validate(existing)],
        mod.CapacityCommitment.model_validate(candidate),
    )
    assert (
        result["admissible"] is False
        and result["clusters"]["2" * 64]["raw_headroom_bytes"] == -192 * GIB
    )
    assert "SHARED_STORAGE_COMMITTED" in result["blocking_codes"]


def test_only_original_generation_and_configuration_receipts_discount_materialized_ram():
    mod = services()
    observed = observation()
    guest = observed.resources.guests[0]
    record = hold(vm_id=45000, memory=16384, disk=0)
    record["guests"][0]["materialization"] = {
        "vmgenid": guest.vmgenid,
        "configuration_revision": guest.configuration_revision,
        "receipt_sha256": "7" * 64,
        "volumes": [],
    }
    original = mod.CapacityCommitment.model_validate(record)
    assert mod.assess(observed, [original], None)["memory_headroom_mib"] == 32768
    value = observed.model_dump()
    value["resources"]["guests"][0]["vmgenid"] = "99999999-2222-4333-8444-555555555555"
    assert (
        mod.assess(CapacityObservation.model_validate(value), [original], None)[
            "memory_headroom_mib"
        ]
        == 16384
    )
    value = observed.model_dump()
    value["resources"]["guests"][0]["configuration_revision"] = "9" * 64
    assert (
        mod.assess(CapacityObservation.model_validate(value), [original], None)[
            "memory_headroom_mib"
        ]
        == 16384
    )


def test_unknown_receipts_vm_names_and_terminal_attempts_do_not_release_capacity():
    mod = services()
    record = hold(vm_id=45000, memory=16384, disk=0)
    result = mod.assess(
        observation(), [mod.CapacityCommitment.model_validate(record)], None
    )
    assert result["memory_headroom_mib"] == 16384
    assert "expires_at" not in mod.CapacityCommitment.model_fields
    retired = mod.CapacityCommitment.model_validate({**record, "phase": "retired"})
    assert mod.assess(observation(), [retired], None)["memory_headroom_mib"] == 32768


def test_same_physical_node_cannot_have_two_active_holds_for_one_guest():
    mod = services()
    with pytest.raises(ValueError):
        mod.assess(
            observation(),
            [mod.CapacityCommitment.model_validate(hold())],
            mod.CapacityCommitment.model_validate(hold("second")),
        )


def test_candidate_cannot_reserve_memory_on_an_unobserved_physical_node():
    mod = services()
    value = hold(physical_node_id="9" * 32)
    value["guests"][0]["storage"] = []
    with pytest.raises(ValueError):
        mod.assess(observation(), [], mod.CapacityCommitment.model_validate(value))


def test_one_original_volume_cannot_discount_two_guest_commitments():
    mod = services()
    observed = observation()
    actual = observed.resources.guests[0]
    first = hold(vm_id=45000, memory=1024, disk=64 * GIB)
    receipt = {
        "vmgenid": actual.vmgenid,
        "configuration_revision": actual.configuration_revision,
        "receipt_sha256": "7" * 64,
        "volumes": [
            {
                "pool_id": "1" * 64,
                "namespace": "",
                "volume": "vm-45000-disk-0",
                "size_bytes": 64 * GIB,
            }
        ],
    }
    first["guests"][0]["materialization"] = receipt
    another = hold("another", vm_id=45001, memory=1024, disk=64 * GIB)
    another["guests"][0]["materialization"] = receipt
    with pytest.raises(ValueError):
        mod.assess(
            observed,
            [mod.CapacityCommitment.model_validate(first)],
            mod.CapacityCommitment.model_validate(another),
        )
