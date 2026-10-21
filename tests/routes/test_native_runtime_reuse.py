"""Reuse trusted runtime code only for a separately retained target identity."""

import json
from pathlib import Path

import pytest

from tests.routes import test_native_environments as environments
from tests.routes.test_native_environments import selection

from tests.core.test_physical_nodes import proof

environment = environments.environment


async def spare_target(db):
    from app.core.models import ProxmoxHost

    async with db.get_session_factory()() as session:
        session.add(
            ProxmoxHost(
                id="spare",
                name="Spare reviewed node",
                api_url="https://192.0.2.20:8006",
                node_name="node-b",
                token_ref="spare@pve!limited=SPARE-PRIVATE-TOKEN",
            )
        )
        await session.commit()


async def bind_spare(db, tmp_path, monkeypatch):
    from app.core import physical_nodes as nodes
    from app.core.preparation_transport import agent_configuration
    from app.core.models import ProxmoxHost

    ssh = tmp_path / "service-ssh-config"
    ssh.write_text(
        "Host range42-preparation-fixture\n  HostName 127.0.0.1\n  User fixture\n"
    )
    ssh.chmod(0o600)
    agents = tmp_path / "agents.json"
    agents.write_text(
        json.dumps(
            {
                "version": 2,
                "installation_id": "c" * 32,
                "agents": [
                    {
                        "target_host_id": "spare",
                        "node_name": "node-b",
                        "ssh_alias": "range42-preparation-fixture",
                        "ssh_config": str(ssh),
                    }
                ],
            }
        )
    )
    agents.chmod(0o600)
    monkeypatch.setenv("RANGE42_PREPARATION_AGENTS_FILE", str(agents))
    configuration = agent_configuration("spare", "node-b")
    async with db.get_session_factory()() as session:
        host = await session.get(ProxmoxHost, "spare")
        observed = await nodes.review_observation(
            session,
            "spare",
            nodes.NodeAttestation(**proof(node_name="node-b")),
            agent_revision=configuration.revision,
            target_revision_at_observation=nodes.target_revision(host),
        )
        await nodes.save_observation(
            session,
            observed,
            review_revision=observed.revision,
            actor="fixture-director",
        )
    return agents, ssh


@pytest.mark.asyncio
async def test_cross_node_reuse_is_explicit_and_requires_a_retained_physical_binding(
    environment,
):
    client, db, _, _ = environment
    await spare_target(db)
    default = await client.post(
        "/v1/environments/review", json=selection(target_host_id="spare")
    )
    assert default.status_code == 409
    explicit = await client.post(
        "/v1/environments/review",
        json=selection(target_host_id="spare", reuse_runtime=True),
    )
    assert explicit.status_code == 409, explicit.text
    assert "physical" in explicit.json()["message"].lower()


@pytest.mark.asyncio
async def test_reviewed_spare_target_uses_isolated_native_views_without_rewriting_the_base(
    environment, tmp_path, monkeypatch
):
    from app.core.models import ProxmoxHost
    from app.core.native_environments import resolve_registered_context

    client, db, workspace, registry = environment
    before = {
        str(p): p.read_bytes() for p in [registry, *workspace.rglob("*")] if p.is_file()
    }
    await spare_target(db)
    await bind_spare(db, tmp_path, monkeypatch)
    payload = selection(target_host_id="spare", reuse_runtime=True)
    reviewed = await client.post("/v1/environments/review", json=payload)
    assert reviewed.status_code == 200, reviewed.text
    value = reviewed.json()
    assert value["runtime_target"]["physical_node_id"] == "a" * 32
    assert value["runtime_target"]["node_name"] == "node-b"
    assert value["infrastructure_qualified"] is False
    created = await client.post(
        "/v1/environments", json={**payload, "review_revision": value["revision"]}
    )
    assert created.status_code == 201, created.text
    identity = created.json()["id"]
    async with db.get_session_factory()() as session:
        context = await resolve_registered_context(
            session, identity, await session.get(ProxmoxHost, "spare")
        )
        assert context.target_host_id == "spare"
        assert (
            context.address == "192.0.2.20"
            and context.node_name == context.target_node == "node-b"
        )
        assert context.workspace == workspace
        assert (
            context.provisioning_credential == "spare@pve!limited=SPARE-PRIVATE-TOKEN"
        )
        assert context.purpose == "workshop" and context.execution_ready is False
    contexts = (await client.get("/v1/contexts")).json()["items"]
    original = next(row for row in contexts if row["id"] == "lab-demo")
    assert original["target_host_id"] == "h" and original["ready"]
    assert next(row for row in contexts if row["id"] == identity)["ready"]
    assert all(Path(p).read_bytes() == data for p, data in before.items())
    public = reviewed.text + created.text + json.dumps(contexts)
    assert "PRIVATE-TOKEN" not in public and str(workspace) not in public


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["credential", "agent_config", "binding_digest"])
async def test_changed_spare_proof_invalidates_review_before_save(
    environment, tmp_path, monkeypatch, changed
):
    from app.core.models import ProxmoxHost
    from app.core.physical_node_models import PhysicalNodeBinding

    client, db, _, _ = environment
    await spare_target(db)
    _, ssh = await bind_spare(db, tmp_path, monkeypatch)
    payload = selection(target_host_id="spare", reuse_runtime=True)
    response = await client.post("/v1/environments/review", json=payload)
    assert response.status_code == 200, response.text
    revision = response.json()["revision"]
    if changed == "agent_config":
        ssh.write_text(ssh.read_text() + "# changed transport\n")
    else:
        async with db.get_session_factory()() as session:
            if changed == "credential":
                (
                    await session.get(ProxmoxHost, "spare")
                ).token_ref = "spare@pve!limited=ROTATED-PRIVATE-TOKEN"
            else:
                (await session.get(PhysicalNodeBinding, "spare")).observation_digest = (
                    "0" * 64
                )
            await session.commit()
    refused = await client.post(
        "/v1/environments", json={**payload, "review_revision": revision}
    )
    assert refused.status_code == 409, refused.text
    assert not (await client.get("/v1/environments")).json()["items"]


@pytest.mark.asyncio
async def test_saved_runtime_identity_cannot_be_replaced_even_by_a_consistent_registry_restore(
    environment, tmp_path, monkeypatch
):
    from app.core.physical_node_models import PhysicalNode, PhysicalNodeBinding
    from app.core.physical_nodes import digest

    client, db, _, _ = environment
    await spare_target(db)
    await bind_spare(db, tmp_path, monkeypatch)
    payload = selection(target_host_id="spare", reuse_runtime=True)
    reviewed = await client.post("/v1/environments/review", json=payload)
    assert reviewed.status_code == 200, reviewed.text
    created = await client.post(
        "/v1/environments",
        json={**payload, "review_revision": reviewed.json()["revision"]},
    )
    assert created.status_code == 201, created.text
    identity = created.json()["id"]
    async with db.get_session_factory()() as session:
        node = await session.get(PhysicalNode, "a" * 32)
        binding = await session.get(PhysicalNodeBinding, "spare")
        attestation = json.loads(binding.observation_json)
        node.hardware_binding = attestation["hardware_binding"] = "9" * 64
        binding.observation_json = json.dumps(attestation)
        binding.observation_digest = digest(
            [binding.target_revision, binding.agent_revision, attestation]
        )
        await session.commit()
    contexts = (await client.get("/v1/contexts")).json()["items"]
    assert next(row for row in contexts if row["id"] == identity)["ready"] is False
    refused = await client.post(f"/v1/environments/{identity}/review")
    assert refused.status_code == 409, refused.text


@pytest.mark.asyncio
async def test_existing_target_default_keeps_its_original_review_shape(environment):
    client, _, _, _ = environment
    implicit = await client.post("/v1/environments/review", json=selection())
    explicit = await client.post(
        "/v1/environments/review", json=selection(reuse_runtime=False)
    )
    assert implicit.status_code == explicit.status_code == 200, explicit.text
    assert implicit.json()["revision"] == explicit.json()["revision"]
