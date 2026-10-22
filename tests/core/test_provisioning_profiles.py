"""An unrelated monitoring scenario uses the shared resource contracts."""

import copy
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError


def profile_data():
    return {
        "version": 1,
        "id": "monitoring",
        "vmid_ranges": [[46000, 46015]],
        "storage": "ceph-range",
        "templates": {
            "sensor": {
                "vm_id": 9250,
                "memory_mib": 2048,
                "cores": 1,
                "disks_gib": [24],
            },
            "collector": {
                "vm_id": 9251,
                "memory_mib": 8192,
                "cores": 3,
                "disks_gib": [48, 96],
            },
            "dashboard": {
                "vm_id": 9252,
                "memory_mib": 1024,
                "cores": 1,
                "disks_gib": [16],
            },
        },
        "networks": [
            {"bridge": "vmbr650", "subnet": "10.80.0.0/28", "gateway": "10.80.0.1"}
        ],
        "managed_gateway": {
            "management_bridge": "vmbr560",
            "management_subnet": "10.80.255.0/29",
        },
    }


def generic_context():
    from app.core.provisioning_profiles import ProvisioningProfile
    from app.core.native_contexts import NativeContext

    return NativeContext(
        id="env-" + "c" * 24,
        label="Monitoring",
        workspace=Path("/runtime/monitoring"),
        context_script=Path("/runtime/context"),
        codename="MONITOR",
        scenario="monitoring",
        address="192.0.2.2",
        target_host_id="monitoring-target",
        target_node="node-b",
        target_api_url="https://pve.example.test:8006",
        provisioning_profile=ProvisioningProfile.model_validate(profile_data()),
        purpose="native",
        execution_ready=False,
    )


def test_resource_contract_has_no_workshop_identity_or_software_requirement():
    from app.core.provisioning_profiles import ProvisioningProfile

    data = profile_data()
    assert ProvisioningProfile.model_validate(data).public() == data
    assert "workshop" not in str(ProvisioningProfile.model_json_schema()).lower()


@pytest.mark.parametrize(
    "damage",
    ["duplicate_template", "run_overlap", "network_overlap", "bool", "command"],
)
def test_generic_bounds_do_not_relax_resource_ownership(damage):
    from app.core.provisioning_profiles import ProvisioningProfile

    data = profile_data()
    if damage == "duplicate_template":
        data["templates"]["collector"]["vm_id"] = 9250
    if damage == "run_overlap":
        data["templates"]["sensor"]["vm_id"] = 46001
    if damage == "network_overlap":
        data["managed_gateway"]["management_subnet"] = "10.80.0.0/29"
    if damage == "bool":
        data["templates"]["sensor"]["cores"] = True
    if damage == "command":
        data["templates"]["sensor"]["command"] = "id"
    with pytest.raises(ValidationError):
        ProvisioningProfile.model_validate(data)


def test_monitoring_network_plan_uses_approved_capability_instead_of_scenario_purpose():
    from app.core.environment_networks import compile_plan

    context = generic_context()
    original = copy.deepcopy(context.provisioning_profile.model_dump())
    plan = compile_plan(context)
    assert plan["owner"] == "range42-environment:" + context.id
    assert plan["networks"] == profile_data()["networks"]
    assert plan["management"]["bridge"] == "vmbr560"
    assert context.provisioning_profile.model_dump() == original
    # Changing a scenario label cannot grant or remove a reviewed capability.
    assert compile_plan(replace(context, purpose="product-lab")) == plan


def test_unqualified_general_environment_still_cannot_start_guests():
    from app.core.native_contexts import execution_issue

    assert execution_issue(generic_context(), "full")
    assert execution_issue(generic_context(), "teardown") == ""


def test_arbitrary_template_set_is_compiled_without_workshop_roles_or_credentials():
    from app.core.template_preparation import TemplatePreparationSpec, compile_templates

    context = generic_context()
    spec = TemplatePreparationSpec.model_validate(
        {
            "namespace": "monitor-base",
            "source_node": "node-a",
            "sources": {"sensor": 9200, "collector": 9201, "dashboard": 9202},
        }
    )
    plan = compile_templates(spec, context, run_id="d" * 16)
    assert plan["kind"] == "native-template-preparation"
    assert [row["role"] for row in plan["templates"]] == [
        "collector",
        "dashboard",
        "sensor",
    ]
    assert {row["vm_id"] for row in plan["templates"]} == {9250, 9251, 9252}
    assert plan["capacity"] == {
        "memory_mib": 11264,
        "vcpus": 5,
        "logical_bytes": 184 * 2**30 + 12 * 2**20,
    }
    assert all(row["nics"] == [] for row in plan["assignments"])
    assert (
        not {"participants", "credentials", "identity_profile", "access_profile"}
        & plan.keys()
    )


@pytest.mark.parametrize(
    "sources",
    [
        {"sensor": 9200},
        {"sensor": 9250},
        {"sensor": 9200, "collector": 9200},
        {"unknown": 9200},
    ],
)
def test_template_recipe_cannot_invent_or_adopt_outputs(sources):
    from app.core.template_preparation import TemplatePreparationSpec, compile_templates
    from app.core.errors import Range42Error

    spec = TemplatePreparationSpec.model_validate(
        {"namespace": "monitor-base", "source_node": "node-a", "sources": sources}
    )
    with pytest.raises(Range42Error):
        compile_templates(spec, generic_context(), run_id="d" * 16)


def installation_context():
    from app.core.provisioning_profiles import ProvisioningProfile

    context = generic_context()
    data = profile_data()
    data["templates"] = {
        "proxmox": {
            "vm_id": 9240,
            "memory_mib": 16384,
            "cores": 4,
            "disks_gib": [32, 128],
        },
        "deployer": {"vm_id": 9241, "memory_mib": 4096, "cores": 2, "disks_gib": [32]},
    }
    data["networks"] = [
        {"bridge": "vmbr650", "subnet": "10.80.0.0/28", "gateway": "10.80.0.1"}
    ]
    return replace(
        context, provisioning_profile=ProvisioningProfile.model_validate(data)
    )


def test_installation_recipe_consumes_general_profile_without_re_registering_environment():
    from app.core.workshop_profiles import installation_profile

    context = installation_context()
    before = context.provisioning_profile.model_dump()
    adapted = installation_profile(context)
    assert adapted.proxmox_template == 9240 and adapted.deployer_template == 9241
    assert adapted.storage == context.provisioning_profile.storage
    assert context.workshop_profile is None
    assert context.provisioning_profile.model_dump() == before


@pytest.mark.parametrize("damage", ["wrong_hardware", "missing_role"])
def test_installation_recipe_does_not_silently_replace_approved_general_hardware(
    damage,
):
    from app.core.workshop_profiles import installation_profile
    from app.core.provisioning_profiles import ProvisioningProfile

    context = installation_context()
    data = context.provisioning_profile.model_dump()
    if damage == "wrong_hardware":
        data["templates"]["proxmox"]["memory_mib"] = 8192
    else:
        del data["templates"]["proxmox"]
    assert (
        installation_profile(
            replace(
                context, provisioning_profile=ProvisioningProfile.model_validate(data)
            )
        )
        is None
    )


def test_general_preparation_can_read_an_explicitly_selected_standard_proxmox_source_id():
    from app.core.template_preparation import TemplatePreparationSpec, compile_templates

    spec = TemplatePreparationSpec.model_validate(
        {
            "namespace": "monitor-base",
            "source_node": "node-a",
            "sources": {"sensor": 100, "collector": 9201, "dashboard": 9202},
        }
    )
    plan = compile_templates(spec, generic_context(), run_id="d" * 16)
    assert (
        next(row for row in plan["templates"] if row["role"] == "sensor")[
            "source_vm_id"
        ]
        == 100
    )
    assert {row["vm_id"] for row in plan["assignments"]} == {9250, 9251, 9252}
