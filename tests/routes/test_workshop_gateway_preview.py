"""Gateway planning and reservation remain in the existing native project flow."""

import json
import os
from sqlalchemy import select
import pytest

from tests.routes import test_workshop_runs as runs
from tests.routes.test_workshop_runs import creation, create_reserved

from tests.core.test_workshop_managed_gateway import request

workshop_env = runs.workshop_env


@pytest.mark.asyncio
async def test_native_preview_includes_gateway_identity_every_nic_and_its_capacity(
    workshop_env,
):
    from app.schemas.v1.allocation import AllocationRequest

    client, _, _ = workshop_env
    payload = creation(managed_gateway=request())
    response = await client.post(
        "/v1/projects/p/native-scenario/preview",
        json={
            "sha": payload["project_sha"],
            "path": payload["native"]["path"],
            "workshop": payload["native"]["workshop"],
        },
    )
    assert response.status_code == 200, response.text
    value = response.json()
    allocated = AllocationRequest.model_validate(
        {**value["allocation_request"], "project_key": "gateway-workshop"}
    )
    assert len(allocated.vms) == 11 and len(allocated.networks) == 6
    assert (allocated.vmid_start, allocated.vmid_end) == (45000, 45010)
    gateway = allocated.vms[-1]
    assert gateway.node_id == "managed-gateway" and gateway.vm_id == 45010
    assert len(gateway.nics) == 6 and gateway.nics[0].ip == "10.78.255.2"
    assert all(
        network.gateway_node_id == gateway.node_id
        for network in allocated.networks
        if network.bridge != "vmbr550"
    )
    assert (
        len(value["topology"]["vms"]) == 11 and len(value["topology"]["networks"]) == 6
    )
    shown = value["topology"]["vms"][-1]
    assert (
        shown["role"] == "gateway"
        and shown["bridge"] == "vmbr550"
        and len(shown["nics"]) == 6
    )
    assert value["capacity"] == {"memory_mib": 106496, "vcpus": 32, "disk_gib": 992}


@pytest.mark.asyncio
async def test_existing_lease_handoff_retains_gateway_with_the_same_workshop_claim(
    workshop_env, monkeypatch
):
    from app.core.allocation_models import DeploymentAllocation
    from app.core.workshop_models import WorkshopRun

    client, db, _ = workshop_env
    registry = os.environ["RANGE42_NATIVE_CONTEXTS_FILE"]
    from pathlib import Path

    file = Path(registry)
    value = json.loads(file.read_text())
    value["contexts"][0]["workshop_profile"]["managed_gateway"] = {
        "management_bridge": "vmbr550",
        "management_subnet": "10.78.255.0/29",
    }
    file.write_text(json.dumps(value))
    response = await create_reserved(client, creation(managed_gateway=request()))
    assert response.status_code == 201, response.text
    async with db.get_session_factory()() as session:
        allocation = await session.scalar(
            select(DeploymentAllocation).where(
                DeploymentAllocation.deployment_id == response.json()["id"]
            )
        )
        assert len(allocation.assignments) == 11
        assert allocation.assignments[-1]["vm_id"] == 45010
        run = await session.get(WorkshopRun, response.json()["id"])
        assert run is not None and len(json.loads(run.credentials)) == 5


@pytest.mark.asyncio
async def test_saved_native_scenario_keeps_the_gateway_in_its_runtime_topology(
    workshop_env, tmp_path
):
    from pathlib import Path
    from app.core.models import Deployment
    from app.core.scenario import prepare_project_scenario

    client, db, _ = workshop_env
    file = Path(os.environ["RANGE42_NATIVE_CONTEXTS_FILE"])
    registry = json.loads(file.read_text())
    registry["contexts"][0]["workshop_profile"]["managed_gateway"] = {
        "management_bridge": "vmbr550",
        "management_subnet": "10.78.255.0/29",
    }
    file.write_text(json.dumps(registry))
    created = await create_reserved(client, creation(managed_gateway=request()))
    assert created.status_code == 201, created.text
    async with db.get_session_factory()() as session:
        deployment = await session.get(Deployment, created.json()["id"])
        prepared = await prepare_project_scenario(
            session, deployment, dest=tmp_path / "runtime-checkout"
        )
    topology = prepared.native["descriptor"]["topology"]
    assert len(topology["vms"]) == 11
    assert topology["vms"][-1]["role"] == "gateway"
    assert len(topology["vms"][-1]["nics"]) == 6
    assert len(topology["networks"]) == 6
    assert 45010 in prepared.vmids
