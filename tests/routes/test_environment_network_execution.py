"""Environment bridges use normal foundation attempts and original Core holds."""

import copy
import importlib
import importlib.util
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text

from app.core.errors import Range42Error
from app.core.physical_nodes import digest
from tests.routes import test_workshop_foundations as base

workshop_env = base.workshop_env
foundation_env = base.foundation_env


def service():
    assert importlib.util.find_spec("app.core.environment_network_execution")
    return importlib.import_module("app.core.environment_network_execution")


@pytest.fixture
async def network_foundation(foundation_env, tmp_path, monkeypatch):
    client, db, _ = foundation_env
    path = tmp_path / "registered-contexts.json"
    registry = json.loads(path.read_text())
    profile = registry["contexts"][0]["workshop_profile"]
    profile["networks"] = [
        {
            "bridge": f"vmbr{600 + index}",
            "subnet": f"10.78.0.{16 * index}/28",
            "gateway": f"10.78.0.{16 * index + 1}",
        }
        for index in range(16)
    ]
    profile["managed_gateway"] = {
        "management_bridge": "vmbr550",
        "management_subnet": "10.78.255.0/29",
    }
    path.write_text(json.dumps(registry))
    selected = {
        "label": "Independent native groups",
        "base_context_id": "lab-demo",
        "target_host_id": "h",
        "workshop_profile": profile,
    }
    review = await client.post("/v1/environments/review", json=selected)
    assert review.status_code == 200, review.text
    registered = await client.post(
        "/v1/environments",
        json={**selected, "review_revision": review.json()["revision"]},
    )
    assert registered.status_code == 201, registered.text
    environment_id = registered.json()["id"]
    preview, deployment = base.preview_request, base.deployment_request
    monkeypatch.setattr(
        base, "preview_request", lambda: {**preview(), "context_id": environment_id}
    )

    def creation():
        payload = deployment()
        payload["native"]["prepare_environment_networks"] = True
        return payload

    monkeypatch.setattr(base, "deployment_request", creation)
    saved, factory, fresh = await base.accepted_foundation(foundation_env)
    from app.core import foundation_execution

    await foundation_execution.journal(factory, fresh)
    node = fresh.observed.node

    async def inspect(_config):
        return node

    async def peer(*_args):
        pass

    monkeypatch.setattr(
        "app.core.preparation_transport.agent_configuration",
        lambda *_: SimpleNamespace(revision="f" * 64),
    )
    monkeypatch.setattr("app.core.preparation_transport.inspect_agent", inspect)
    monkeypatch.setattr("app.core.preparation_transport.verify_api_peer", peer)
    configuration = SimpleNamespace(
        revision="a" * 64,
        installation_id=node.installation_id,
        node_name=node.node_name,
    )
    monkeypatch.setattr(
        "app.core.environment_network_transport.configuration", lambda *_: configuration
    )
    policy = {
        "version": 1,
        "node_name": node.node_name,
        "installation_id": node.installation_id,
        "networks": copy.deepcopy(profile["networks"]),
        "management": {
            "bridge": "vmbr550",
            "subnet": "10.78.255.0/29",
            "host_address": "10.78.255.1/29",
        },
        "uplink": {
            "interface": "vmbr0",
            "address": "10.42.0.2/30",
            "gateway": "10.42.0.1",
        },
        "routing_table": 42550,
        "routing_priority": 4250,
        "protected_public_cidrs": ["31.22.122.64/26"],
    }
    root_profile = {
        "protocol": "range42-environment-network-profile-v1",
        "policy": policy,
        "policy_revision": digest(policy),
        "node": node.model_dump(),
        "supported_operations": ["profile", "prepare", "status"],
        "execution_ready": False,
    }
    calls = []

    async def rpc(_configuration, *, action, request=None):
        calls.append((action, request))
        # This is a native I/O boundary: no Core write transaction may span it.
        async with factory() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            await session.rollback()
        if action == "profile":
            return copy.deepcopy(root_profile)
        raise AssertionError("Review must never apply networks")

    monkeypatch.setattr("app.core.environment_network_transport.read", rpc)
    return (
        client,
        factory,
        saved,
        fresh,
        environment_id,
        configuration,
        root_profile,
        calls,
    )


@pytest.mark.asyncio
async def test_network_request_is_environment_owned_and_preserves_the_original_template_hold(
    network_foundation,
):
    from app.core.capacity_models import CapacityHold
    from app.core.native_environment_models import NativeEnvironment
    from app.core import capacity_ledger

    _, factory, saved, _, environment_id, _, _, calls = network_foundation
    async with factory() as session:
        row = await session.scalar(select(CapacityHold))
        original_digest, root_request = row.record_digest, row.preparation_request_json
        with pytest.raises(Range42Error):
            await capacity_ledger.assert_dispatch_accepted(session, saved["id"])
    bundle = await service().journal(factory, saved["id"])
    assert bundle["request"]["plan"]["owner"] == "range42-environment:" + environment_id
    assert (
        bundle["request"]["foundation_request"]["plan"]
        == saved["native"]["foundation_plan"]
    )
    assert calls and all(action == "profile" for action, _ in calls)
    assert await service().journal(factory, saved["id"]) == bundle
    async with factory() as session:
        row = await session.scalar(select(CapacityHold))
        assert (row.record_digest, row.preparation_request_json) == (
            original_digest,
            root_request,
        )
        capacity_ledger.commitment(row)
        stored = await session.get(NativeEnvironment, environment_id)
        assert (
            json.loads(stored.network_preparation_json)["request"] == bundle["request"]
        )
        await capacity_ledger.assert_dispatch_accepted(session, saved["id"])


@pytest.mark.asyncio
async def test_network_service_rotation_does_not_change_original_capacity_but_closes_its_own_dispatch(
    network_foundation, monkeypatch
):
    from app.core.capacity_models import CapacityHold
    from app.core import capacity_ledger

    _, factory, saved, _, _, _, _, _ = network_foundation
    await service().journal(factory, saved["id"])
    async with factory() as session:
        original = (await session.scalar(select(CapacityHold))).record_digest
    monkeypatch.setattr(
        "app.core.environment_network_transport.configuration",
        lambda *_: SimpleNamespace(revision="b" * 64),
    )
    async with factory() as session:
        with pytest.raises(Range42Error):
            await capacity_ledger.assert_dispatch_accepted(session, saved["id"])
        assert (await session.scalar(select(CapacityHold))).record_digest == original


@pytest.mark.asyncio
async def test_overview_reviews_network_intent_without_new_worker_or_host_mutation(
    network_foundation,
):
    client, _, saved, _, environment_id, _, _, calls = network_foundation
    prefix = f"/v1/deployments/{saved['id']}/foundation/networks"
    before = await client.get(prefix)
    assert before.status_code == 200, before.text
    assert before.json()["requested"] and not before.json()["request_recorded"]
    response = await client.post(prefix + "/review", json={})
    assert response.status_code == 200, response.text
    assert (
        response.json()["environment_id"] == environment_id
        and response.json()["preparation_available"]
    )
    assert (
        response.json()["receipt"] is None
        and response.json()["execution_ready"] is False
    )
    assert all(action == "profile" for action, _ in calls)
    extra = await client.post(prefix + "/review", json={"command": "id"})
    assert extra.status_code == 422


@pytest.mark.asyncio
async def test_network_runner_reconciles_before_applying_only_the_original_request(
    network_foundation,
):
    from tests.core.test_environment_network_receipts import receipt

    _, factory, saved, _, _, configuration, profile, _ = network_foundation
    bundle = await service().journal(factory, saved["id"])
    original = bundle["request"]
    calls = []
    final = receipt()
    final.update(
        request_digest=digest(original),
        policy_revision=original["policy_revision"],
        environment_id=original["plan"]["environment_id"],
        node=profile["node"],
    )
    final["proof"]["request_digest"] = digest(original)
    final["proof"]["owner"].update(
        {
            field: profile["node"][field]
            for field in [
                "physical_node_id",
                "hardware_binding",
                "installation_id",
                "node_name",
            ]
        }
    )

    async def rpc(_configuration, *, action, request=None):
        calls.append((action, request))
        value = copy.deepcopy(final)
        if action == "profile":
            return profile
        if len(calls) == 2:
            value.update(status="absent", network_ready=False, proof=None)
        return value

    result = await service().run_original(configuration, original, rpc=rpc)
    assert result == final
    assert [action for action, _ in calls] == ["profile", "status", "prepare", "status"]
    assert all(request == original for action, request in calls if action != "profile")


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["request", "policy", "foundation", "environment"])
async def test_retained_network_journal_cannot_adopt_a_changed_original(
    network_foundation, damage
):
    from app.core.native_environment_models import NativeEnvironment

    _, factory, saved, _, environment_id, _, _, _ = network_foundation
    await service().journal(factory, saved["id"])
    async with factory() as session:
        row = await session.get(NativeEnvironment, environment_id)
        value = json.loads(row.network_preparation_json)
        if damage == "request":
            value["request"]["plan"]["networks"][0]["bridge"] = "vmbr999"
        if damage == "policy":
            value["profile"]["policy"]["routing_table"] = 42551
        if damage == "foundation":
            value["request"]["foundation_request"]["plan"]["run_id"] = "f" * 16
        if damage == "environment":
            value["request"]["plan"]["environment_id"] = "env-" + "f" * 24
        row.network_preparation_json = json.dumps(value)
        await session.commit()
    with pytest.raises(Range42Error):
        await service().dispatch_inputs(factory, saved["id"])
