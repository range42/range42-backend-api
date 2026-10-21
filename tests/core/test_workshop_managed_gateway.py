"""A managed gateway is owned by its run in the existing canonical allocation."""

from copy import deepcopy
from ipaddress import IPv4Network

import pytest
from pydantic import ValidationError

from tests.core.test_workshop_runs import specification


def request(**changes):
    return {
        "vm_id": 45010,
        "management_bridge": "vmbr550",
        "management_subnet": "10.78.255.0/29",
        "management_ip": "10.78.255.2",
        **changes,
    }


def compile_gateway(**changes):
    from app.core.workshop_runs import WorkshopSpec, compile_plan

    return compile_plan(
        WorkshopSpec.model_validate(
            specification(
                participants_per_environment=2,
                bridge_start=600,
                proxmox_template=9240,
                deployer_template=9241,
                managed_gateway=request(**changes),
            )
        ),
        run_id="a" * 32,
    )


def test_gateway_is_a_separately_owned_nonparticipant_guest_in_the_original_plan():
    plan = compile_gateway()
    assert len(plan["participants"]) == 5 and len(plan["guests"]) == 10
    gateway = plan["managed_gateway"]
    assert gateway["vm_id"] == 45010 and gateway["template"] == 9241
    assert gateway["owner"] == "range42-workshop:" + "a" * 32 + ":gateway"
    assert gateway["alias"] == "r42.training-a.gateway"
    assert (gateway["cores"], gateway["memory_mib"], gateway["disk_gib"]) == (
        2,
        4096,
        32,
    )
    assert len(gateway["nics"]) == 6
    assert gateway["nics"][0] == {
        "index": 0,
        "bridge": "vmbr550",
        "ip": "10.78.255.2",
        "prefix": 29,
    }
    assert [nic["ip"] for nic in gateway["nics"][1:]] == [
        network["gateway"] for network in plan["networks"]
    ]
    assert len(plan["assignments"]) == 11
    assert plan["assignments"][-1] == {
        "vm_id": 45010,
        "vm_name": "training-a-gateway",
        "run_namespace": "training-a",
        "nics": [
            {key: nic[key] for key in ["index", "bridge", "ip"]}
            for nic in gateway["nics"]
        ],
    }
    assert plan["capacity"] == {"memory_mib": 106496, "vcpus": 32, "disk_gib": 992}
    # Guest passwords and participant mappings keep their existing two-VM contract.
    assert all(guest["role"] in {"proxmox", "deployer"} for guest in plan["guests"])


def test_legacy_native_plan_is_byte_compatible_when_managed_gateway_is_absent():
    from app.core.workshop_runs import WorkshopSpec, compile_plan

    old = compile_plan(WorkshopSpec.model_validate(specification()), run_id="a" * 32)
    explicit = compile_plan(
        WorkshopSpec.model_validate(specification(managed_gateway=None)),
        run_id="a" * 32,
    )
    assert old == explicit
    assert "managed_gateway" not in old
    assert len(old["assignments"]) == 10
    assert old["capacity"] == {"memory_mib": 102400, "vcpus": 30, "disk_gib": 960}


@pytest.mark.parametrize(
    "change",
    [
        {"vm_id": True},
        {"vm_id": 45000},
        {"vm_id": 9240},
        {"vm_id": 9241},
        {"management_ip": "10.78.255.0"},
        {"management_ip": "10.78.255.1"},
        {"management_ip": "10.78.255.7"},
        {"management_ip": "10.78.254.2"},
        {"management_subnet": "0.0.0.0/0"},
        {"management_subnet": "203.0.113.0/29"},
        {"management_subnet": "10.78.255.1/29"},
        {"management_subnet": "10.78.255.0/24"},
        {"management_bridge": "vmbr0600"},
        {"management_bridge": "vmbr600"},
        {"management_subnet": "10.78.0.0/29", "management_ip": "10.78.0.2"},
        {"command": "id"},
        {"memory_mib": 1},
        {"template": 9000},
    ],
)
def test_gateway_inputs_cannot_collide_with_guests_templates_or_participant_networks(
    change,
):
    with pytest.raises(ValidationError):
        compile_gateway(**change)


def profile(**changes):
    from app.core.workshop_profiles import WorkshopProfile

    return WorkshopProfile.model_validate(
        {
            "version": 1,
            "id": "workshop",
            "vmid_ranges": [[45000, 45039]],
            "networks": [
                {
                    "bridge": f"vmbr{600 + i}",
                    "subnet": str(network),
                    "gateway": str(network[1]),
                }
                for i, network in enumerate(
                    IPv4Network("10.78.0.0/24").subnets(new_prefix=28)
                )
            ],
            "proxmox_template": 9240,
            "deployer_template": 9241,
            "storage": "ceph-range",
            **changes,
        }
    )


def test_gateway_needs_an_explicit_optional_provisioning_policy():
    from app.core.workshop_profiles import require_workshop_profile
    from app.core.errors import Range42Error
    from types import SimpleNamespace

    plan = compile_gateway()
    with pytest.raises(Range42Error) as error:
        require_workshop_profile(SimpleNamespace(workshop_profile=profile()), plan)
    assert error.value.code == "WORKSHOP_GATEWAY_PROFILE_REQUIRED"
    policy = {"management_bridge": "vmbr550", "management_subnet": "10.78.255.0/29"}
    approved = profile(managed_gateway=policy)
    assert (
        require_workshop_profile(SimpleNamespace(workshop_profile=approved), plan)
        == approved
    )
    assert "managed_gateway" not in profile().public()
    assert approved.public()["managed_gateway"] == policy
    for field, changed in [
        ("vm_id", 45040),
        ("management_bridge", "vmbr551"),
        ("management_subnet", "10.79.255.0/29"),
    ]:
        invalid = deepcopy(plan)
        invalid["managed_gateway"][field] = changed
        with pytest.raises(Range42Error):
            require_workshop_profile(
                SimpleNamespace(workshop_profile=approved), invalid
            )


from tests.core import test_capacity_ledger as ledger_tests  # noqa: E402

ledger = ledger_tests.ledger


@pytest.mark.asyncio
async def test_actual_capacity_review_charges_the_gateway_and_all_original_participants(
    ledger,
):
    from sqlalchemy import select
    from app.core.models import Deployment
    from app.core.allocation_models import DeploymentAllocation
    from app.core.workshop_runs import WorkshopSpec, compile_plan
    from tests.core.test_capacity_accounting import observation

    mod, factory = ledger
    spec = WorkshopSpec.model_validate(
        specification(
            participants_per_environment=2,
            bridge_start=600,
            proxmox_template=9240,
            deployer_template=9241,
            managed_gateway=request(),
        )
    )
    plan = compile_plan(spec, run_id="a" * 16)
    async with factory() as session:
        dep = await session.get(Deployment, "a" * 16)
        dep.native = {
            **dep.native,
            "workshop": spec.model_dump(),
            "workshop_plan": plan,
        }
        claim = await session.scalar(
            select(DeploymentAllocation).where(
                DeploymentAllocation.deployment_id == dep.id
            )
        )
        claim.assignments = plan["assignments"]
        await session.commit()
    reviewed = await mod.review(
        factory, "a" * 16, observation(), agent_revision="f" * 64
    )
    assert len(reviewed.candidate.guests) == 11
    assert sum(guest.memory_mib for guest in reviewed.candidate.guests) == 106496
    assert sum(guest.vcpus for guest in reviewed.candidate.guests) == 32
    assert (
        sum(
            disk.logical_bytes
            for guest in reviewed.candidate.guests
            for disk in guest.storage
        )
        == 992 * 2**30
    )
    assert reviewed.candidate.guests[-1].vm_id == 45010


def test_managed_gateway_policy_cannot_share_a_retained_participant_router():
    infrastructure = {
        "vmid": 42130,
        "node": "node",
        "vmgenid": "12345678-1234-1234-1234-123456789abc",
        "owner": "retained-gateway",
        "nics": [
            {"interface": "net0", "bridge": "vmbr600", "mac": "02:00:00:00:00:01"}
        ],
    }
    policy = {"management_bridge": "vmbr550", "management_subnet": "10.78.255.0/29"}
    with pytest.raises(ValidationError):
        profile(managed_gateway=policy, infrastructure_guests=[infrastructure])
    from app.core.native_environments import EnvironmentSelection

    with pytest.raises(ValidationError):
        EnvironmentSelection(
            label="Managed gateways",
            base_context_id="base",
            target_host_id="target",
            workshop_profile=profile(managed_gateway=policy),
            gateway_foundation={
                **infrastructure,
                "name": "Retained gateway",
                "configuration_revision": "a" * 64,
            },
        )


def test_enrolled_gateway_policy_cannot_be_bypassed_by_omitting_the_run_gateway():
    from types import SimpleNamespace
    from app.core.errors import Range42Error
    from app.core.workshop_profiles import require_workshop_profile

    plan = compile_gateway()
    del plan["managed_gateway"]
    approved = profile(
        managed_gateway={
            "management_bridge": "vmbr550",
            "management_subnet": "10.78.255.0/29",
        }
    )
    with pytest.raises(Range42Error) as error:
        require_workshop_profile(SimpleNamespace(workshop_profile=approved), plan)
    assert error.value.code == "WORKSHOP_GATEWAY_REQUIRED"
