from copy import deepcopy

import pytest


def manifest():
    return {"version": 3, "scenario": "lab", "vms": [{
        "vm_id": 3101, "vm_name": "router", "template_vm_id": 9901,
        "ip": "10.42.1.10", "bridge": "r42lan", "role": "vm",
        "nics": [{"index": 0, "ip": "10.42.1.10", "bridge": "r42lan", "prefix": 24},
                 {"index": 1, "ip": "10.42.2.10", "bridge": "r42dmz", "prefix": 24}],
    }]}


def test_additive_v3_preserves_legacy_management_address():
    from app.core.scenario_manifest import validate_vm_manifest
    result = validate_vm_manifest(manifest())
    assert result["vms"][0]["ip"] == "10.42.1.10"
    assert len(result["vms"][0]["nics"]) == 2
    legacy = {"vms": [{"vm_id": 3101}]}
    assert validate_vm_manifest(legacy) == legacy


@pytest.mark.parametrize("change", [
    lambda vm: vm.update(ip="10.42.2.10"),
    lambda vm: vm["nics"][1].update(index=0),
    lambda vm: vm["nics"][1].update(ip="invalid"),
    lambda vm: vm.update(memory_mb=0),
    lambda vm: vm.update(cores=True),
    lambda vm: vm.update(disk_gb=10, disk_device="ide2"),
])
def test_v3_rejects_ambiguous_management_nics_and_invalid_resources(change):
    from app.core.scenario_manifest import validate_vm_manifest
    value = manifest()
    change(value["vms"][0])
    with pytest.raises(ValueError):
        validate_vm_manifest(value)


def test_collisions_include_secondary_nics_of_other_vms():
    from app.core.scenario_manifest import validate_vm_manifest
    value = manifest()
    second = deepcopy(value["vms"][0])
    second.update(vm_id=3102, vm_name="second", ip="10.42.1.11")
    second["nics"][0]["ip"] = second["ip"]
    value["vms"].append(second)
    with pytest.raises(ValueError, match="address"):
        validate_vm_manifest(value)


@pytest.mark.parametrize('storage', ['../pool', '{{ pool }}', 'bad pool', 'a' * 65, 42])
def test_reviewed_storage_rejects_invalid_pool_identifiers(storage):
    from app.core.scenario_manifest import validate_vm_manifest
    value = manifest()
    value['guest_preferences_version'] = 1
    value['vms'][0]['storage'] = storage
    with pytest.raises(ValueError):
        validate_vm_manifest(value)


def test_storage_requires_complete_explicit_versioned_preferences():
    from app.core.scenario_manifest import validate_vm_manifest
    value = manifest()
    value['vms'][0]['storage'] = 'fast-pool'
    with pytest.raises(ValueError, match='preferences'):
        validate_vm_manifest(value)
    value['guest_preferences_version'] = 1
    assert validate_vm_manifest(value) == value
    value['vms'][0]['storage'] = None
    assert validate_vm_manifest(value) == value
    del value['vms'][0]['storage']
    with pytest.raises(ValueError, match='storage'):
        validate_vm_manifest(value)
