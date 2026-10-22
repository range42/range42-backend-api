"""Public registration and catalog preparation need no workshop registration."""

import json
import pytest
from sqlalchemy import select
from tests.routes import test_native_environments as env_base
from tests.routes import test_workshop_foundations as base

from tests.routes.test_allocation_reservations import HEADERS
from tests.core.test_provisioning_profiles import profile_data


environment = env_base.environment
foundation_env = base.foundation_env
workshop_env = base.workshop_env


@pytest.mark.asyncio
async def test_generic_environment_review_registration_discovery_preserves_execution_gate(
    environment,
):
    client, db, _, _ = environment
    payload = {
        "label": "Monitoring lab",
        "base_context_id": "lab-demo",
        "target_host_id": "workshops",
        "provisioning_profile": profile_data(),
    }
    response = await client.post("/v1/environments/review", json=payload)
    assert response.status_code == 200, response.text
    review = response.json()
    assert review["provisioning_profile"] == profile_data()
    assert review.get("workshop_profile") is None
    saved = await client.post(
        "/v1/environments", json={**payload, "review_revision": review["revision"]}
    )
    assert saved.status_code == 201, saved.text
    ident = saved.json()["id"]
    contexts = (await client.get("/v1/contexts")).json()["items"]
    selected = next(row for row in contexts if row["id"] == ident)
    assert selected["provisioning_profile"] == profile_data()
    assert selected["purpose"] == "native" and selected["execution_ready"] is False
    assert selected.get("workshop_profile") is None
    assert (await client.get("/v1/environments")).json()["items"][0][
        "provisioning_profile"
    ] == profile_data()


@pytest.mark.asyncio
async def test_monitoring_template_scenario_uses_existing_reservation_and_deployment_ledgers(
    foundation_env, tmp_path, monkeypatch
):
    client, db, repo = foundation_env
    registry = tmp_path / "registered-contexts.json"
    data = json.loads(registry.read_text())
    del data["contexts"][0]["workshop_profile"]
    data["contexts"][0]["provisioning_profile"] = profile_data()
    registry.write_text(json.dumps(data))
    manifest = repo / "scenarios/installer-course/manifest/preparation.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "native-template-preparation",
                "title": "Monitoring template set",
            }
        )
    )
    payload = {
        "sha": "a" * 40,
        "path": "scenarios/installer-course",
        "context_id": "lab-demo",
        "target_host_id": "h",
        "foundation": {
            "namespace": "monitor-base",
            "source_node": "node-a",
            "sources": {"sensor": 9200, "collector": 9201, "dashboard": 9202},
        },
    }
    prefix = "/v1/projects/p/native-scenario/foundation"
    response = await client.post(prefix + "/preview", json=payload)
    assert response.status_code == 200, response.text
    assert len(response.json()["assignments"]) == 3
    reserved = await client.post(
        prefix + "/reservations",
        json={**payload, "project_key": "monitoring-owner"},
        headers=HEADERS,
    )
    assert reserved.status_code == 200, reserved.text
    created = await client.post(
        "/v1/deployments/",
        json={
            "project_id": "p",
            "project_sha": "a" * 40,
            "codename": "MONITOR",
            "scenario_label": "monitoring-base",
            "target_host_id": "h",
            "team_count": 1,
            "allocation_reservation_id": reserved.json()["reservation_id"],
            "native": {
                "context_id": "lab-demo",
                "path": payload["path"],
                "foundation": payload["foundation"],
            },
        },
        headers=HEADERS,
    )
    assert created.status_code == 201, created.text
    from app.core.allocation_models import DeploymentAllocation
    from app.core.workshop_models import WorkshopRun

    async with db.get_session_factory()() as session:
        claims = list(await session.scalars(select(DeploymentAllocation)))
        assert len(claims) == 1 and len(claims[0].assignments) == 3
        assert not list(await session.scalars(select(WorkshopRun)))
    assert (
        created.json()["native"]["foundation_plan"]["kind"]
        == "native-template-preparation"
    )
    # The same immutable plan must pass the existing physical hold and original request journal.
    from app.core import foundation_execution, capacity_ledger
    import copy
    from types import SimpleNamespace

    monkeypatch.setattr(base, "preview_request", lambda: payload)
    monkeypatch.setattr(
        base, "created_foundation", lambda _: _saved(created.json(), client, db)
    )
    saved, factory, fresh = await base.accepted_foundation(foundation_env)
    original = await foundation_execution.journal(factory, fresh)
    assert len(original["capacity_request"]["commitment"]["guests"]) == 3
    complete = {
        "protocol": "range42-foundation-receipt-v1",
        "request_id": original["capacity_request"]["request_id"],
        "request_digest": __import__(
            "app.core.physical_nodes", fromlist=["digest"]
        ).digest(original),
        "status": "complete",
        "node": fresh.observed.node.model_dump(),
        "foundation_ready": True,
        "execution_ready": False,
        "infrastructure_qualified": False,
        "templates": [],
    }
    for row in original["plan"]["templates"]:
        complete["templates"].append(
            {
                "vm_id": row["vm_id"],
                "phase": "complete",
                "vmgenid": f"aaaaaaaa-bbbb-4ccc-8ddd-{row['vm_id']:012d}",
                "configuration_revision": "c" * 64,
                "volumes": [
                    f"ceph-range:base-{row['vm_id']}-disk-{index}"
                    for index in range(len(row["disks_gib"]))
                ]
                + [f"ceph-range:vm-{row['vm_id']}-cloudinit"],
            }
        )

    async def rpc(_config, **options):
        assert options["request"] == original
        return copy.deepcopy(complete)

    assert (
        await foundation_execution.run_original(SimpleNamespace(), original, rpc=rpc)
    )["foundation_ready"]
    from app.core.capacity_models import CapacityHold

    async with factory() as session:
        hold = await session.scalar(
            select(CapacityHold).where(CapacityHold.deployment_id == saved["id"])
        )
        assert len(capacity_ledger.commitment(hold).guests) == 3
        from app.core.errors import Range42Error

        with pytest.raises(Range42Error) as refused:
            await capacity_ledger.assert_vm_capacity_accepted(session, 9250)
        assert getattr(refused.value, "status", None) == 409


async def _saved(saved, client, db):
    return saved, client, db


@pytest.mark.asyncio
async def test_optional_networks_are_visible_in_generic_preview_but_not_borrowed_by_template_lease(
    foundation_env,
):
    client, _, repo = foundation_env
    (repo / "scenarios/installer-course/manifest/preparation.json").write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "native-template-preparation",
                "title": "Monitoring templates",
            }
        )
    )
    selection = {
        "label": "Monitoring",
        "base_context_id": "lab-demo",
        "target_host_id": "h",
        "provisioning_profile": profile_data(),
    }
    review = await client.post("/v1/environments/review", json=selection)
    assert review.status_code == 200, review.text
    env = await client.post(
        "/v1/environments",
        json={**selection, "review_revision": review.json()["revision"]},
    )
    assert env.status_code == 201, env.text
    payload = {
        "sha": "a" * 40,
        "path": "scenarios/installer-course",
        "context_id": env.json()["id"],
        "target_host_id": "h",
        "foundation": {
            "namespace": "monitor-base",
            "source_node": "node-a",
            "sources": {"collector": 9201, "dashboard": 9202, "sensor": 9200},
        },
        "prepare_environment_networks": True,
    }
    response = await client.post(
        "/v1/projects/p/native-scenario/foundation/preview", json=payload
    )
    assert response.status_code == 200, response.text
    value = response.json()
    assert {row["vnet"] for row in value["topology"]["networks"]} == {
        "vmbr650",
        "vmbr560",
    }
    assert (
        value["environment_networks"]["owner"]
        == "range42-environment:" + env.json()["id"]
    )
    assert value["allocation_request"]["networks"] == []
    assert all(row["nics"] == [] for row in value["allocation_request"]["vms"])


@pytest.mark.asyncio
async def test_installation_recipe_creates_a_run_from_general_resource_bounds(
    workshop_env, tmp_path
):
    from tests.routes import test_workshop_runs as recipe

    client, _, _ = workshop_env
    registry = tmp_path / "registered-contexts.json"
    data = json.loads(registry.read_text())
    original = data["contexts"][0].pop("workshop_profile")
    general = {
        key: value
        for key, value in original.items()
        if key not in {"proxmox_template", "deployer_template"}
    }
    general["templates"] = {
        "proxmox": {
            "vm_id": original["proxmox_template"],
            "memory_mib": 16384,
            "cores": 4,
            "disks_gib": [32, 128],
        },
        "deployer": {
            "vm_id": original["deployer_template"],
            "memory_mib": 4096,
            "cores": 2,
            "disks_gib": [32],
        },
    }
    data["contexts"][0]["provisioning_profile"] = general
    registry.write_text(json.dumps(data))
    response = await recipe.create_reserved(client, recipe.creation())
    assert response.status_code == 201, response.text
    assert len(response.json()["native"]["workshop_plan"]["guests"]) == 10
    assert "workshop_profile" not in json.loads(registry.read_text())["contexts"][0]
