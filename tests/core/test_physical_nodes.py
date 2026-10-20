"""Aliases must identify one retained physical node before capacity accounting."""

import asyncio
from importlib.util import find_spec

import pytest
from sqlalchemy import select, func

from app.core.db import build_engine, session_factory
from app.core.models import Base, ProxmoxHost


def services():
    assert find_spec("app.core.physical_nodes") is not None, (
        "Missing protected physical-node binding registry"
    )
    from app.core import physical_nodes

    return physical_nodes


def proof(**changes):
    return {
        "protocol": "range42-preparation-agent-v1",
        "physical_node_id": "a" * 32,
        "hardware_binding": "b" * 64,
        "node_name": "pve-test",
        "installation_id": "c" * 32,
        "configuration_revision": "d" * 64,
        "api_certificate_sha256": "e" * 64,
        "node_certificate_sha256": changes.get(
            "node_certificate_sha256", changes.get("api_certificate_sha256", "e" * 64)
        ),
        "boot_id": "11111111-2222-4333-8444-555555555555",
        "supported_operations": ["inspect"],
        "execution_ready": False,
        "infrastructure_qualified": False,
        **changes,
    }


@pytest.fixture
async def registry(tmp_path):
    mod = services()
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/nodes.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = session_factory(engine)
    async with factory() as session:
        session.add_all(
            [
                ProxmoxHost(
                    id=id,
                    name=id,
                    api_url=url,
                    node_name="pve-test",
                    token_ref="synthetic@pve!key=PRIVATE",
                )
                for id, url in [
                    ("first", "https://pve-a.invalid:8006"),
                    ("alias", "https://pve-b.invalid:8006"),
                ]
            ]
        )
        await session.commit()
    yield mod, factory
    await engine.dispose()


async def observe(mod, session, host_id="first", **changes):
    host = await session.get(ProxmoxHost, host_id)
    return await mod.review_observation(
        session,
        host_id,
        mod.NodeAttestation(**proof(**changes)),
        agent_revision="f" * 64,
        target_revision_at_observation=mod.target_revision(host),
    )


@pytest.mark.asyncio
async def test_aliases_and_different_credentials_cannot_form_two_physical_capacity_pools(
    registry,
):
    mod, factory = registry
    for host_id in ["first", "alias"]:
        async with factory() as session:
            review = await observe(mod, session, host_id)
            saved = await mod.save_observation(
                session,
                review,
                review_revision=review.revision,
                actor="fixture-director",
            )
            assert saved["physical_node_id"] == "a" * 32
            assert saved["execution_ready"] is False
    from app.core.physical_node_models import PhysicalNode, PhysicalNodeBinding

    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(PhysicalNode)) == 1
        assert (
            await session.scalar(select(func.count()).select_from(PhysicalNodeBinding))
            == 2
        )


@pytest.mark.asyncio
async def test_lost_save_reply_returns_the_original_binding_without_another_node(
    registry,
):
    mod, factory = registry
    async with factory() as session:
        observed = await observe(mod, session)
        first = await mod.save_observation(
            session,
            observed,
            review_revision=observed.revision,
            actor="fixture-director",
        )
    async with factory() as session:
        repeated = await observe(mod, session)
        second = await mod.save_observation(
            session,
            repeated,
            review_revision=observed.revision,
            actor="fixture-director",
        )
    assert first == second


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes", [{"physical_node_id": "9" * 32}, {"hardware_binding": "8" * 64}]
)
async def test_lost_identity_or_replaced_hardware_never_creates_a_new_pool(
    registry, changes
):
    mod, factory = registry
    async with factory() as session:
        review = await observe(mod, session)
        await mod.save_observation(
            session, review, review_revision=review.revision, actor="fixture-director"
        )
    from app.core.errors import Range42Error

    async with factory() as session:
        with pytest.raises(Range42Error) as error:
            review = await observe(mod, session, "alias", **changes)
            await mod.save_observation(
                session,
                review,
                review_revision=review.revision,
                actor="fixture-director",
            )
        assert error.value.code == "PHYSICAL_NODE_IDENTITY_CONFLICT"


@pytest.mark.asyncio
async def test_credential_or_target_drift_during_observation_refuses_save(registry):
    mod, factory = registry
    async with factory() as session:
        review = await observe(mod, session)
    async with factory() as session:
        (
            await session.get(ProxmoxHost, "first")
        ).token_ref = "synthetic@pve!key=ROTATED-PRIVATE"
        await session.commit()
    from app.core.errors import Range42Error

    async with factory() as session:
        with pytest.raises(Range42Error) as error:
            await mod.save_observation(
                session,
                review,
                review_revision=review.revision,
                actor="fixture-director",
            )
        assert error.value.code == "PHYSICAL_NODE_REVIEW_REQUIRED"


@pytest.mark.asyncio
async def test_target_changed_during_network_observation_cannot_be_silently_reviewed(
    registry,
):
    mod, factory = registry
    async with factory() as session:
        previous = mod.target_revision(await session.get(ProxmoxHost, "first"))
    async with factory() as session:
        (
            await session.get(ProxmoxHost, "first")
        ).api_url = "https://replacement.invalid:8006"
        await session.commit()
    from app.core.errors import Range42Error

    async with factory() as session:
        with pytest.raises(Range42Error) as error:
            await mod.review_observation(
                session,
                "first",
                mod.NodeAttestation(**proof()),
                agent_revision="f" * 64,
                target_revision_at_observation=previous,
            )
        assert error.value.code == "PHYSICAL_NODE_REVIEW_REQUIRED"


@pytest.mark.asyncio
async def test_concurrent_reviews_cannot_overwrite_each_other_or_duplicate_a_node(
    registry,
):
    mod, factory = registry
    async with factory() as session:
        left = await observe(mod, session, "first")
    async with factory() as session:
        right = await observe(mod, session, "alias")

    async def save(review):
        async with factory() as session:
            try:
                return await mod.save_observation(
                    session,
                    review,
                    review_revision=review.revision,
                    actor="fixture-director",
                )
            except Exception as error:
                return error

    results = await asyncio.gather(save(left), save(right))
    assert sum(isinstance(value, dict) for value in results) == 1
    errors = [value for value in results if not isinstance(value, dict)]
    assert errors[0].code == "PHYSICAL_NODE_REVIEW_REQUIRED"


def test_agent_cannot_assert_readiness_or_supply_unrecognized_execution_capabilities():
    mod = services()
    from pydantic import ValidationError

    for changes in [
        {"execution_ready": True},
        {"infrastructure_qualified": True},
        {"execution_ready": 0},
        {"infrastructure_qualified": 0},
        {"supported_operations": ["inspect", "shell"]},
        {"command": "id"},
        {"api_certificate_sha256": None},
    ]:
        with pytest.raises(ValidationError):
            mod.NodeAttestation(**proof(**changes))


@pytest.mark.asyncio
async def test_binding_records_the_server_actor_and_lost_reply_does_not_reassign_approval(
    registry,
):
    mod, factory = registry
    async with factory() as session:
        reviewed = await observe(mod, session)
        first = await mod.save_observation(
            session, reviewed, review_revision=reviewed.revision, actor="staff-one"
        )
    assert first["approved_by"] == "staff-one"
    async with factory() as session:
        current = await observe(mod, session)
        again = await mod.save_observation(
            session, current, review_revision=reviewed.revision, actor="staff-two"
        )
    assert again == first


@pytest.mark.asyncio
async def test_lost_node_row_with_retained_aliases_cannot_create_a_replacement_pool(
    registry,
):
    from sqlalchemy import text

    mod, factory = registry
    async with factory() as session:
        reviewed = await observe(mod, session)
        await mod.save_observation(
            session,
            reviewed,
            review_revision=reviewed.revision,
            actor="fixture-director",
        )
    async with factory() as session:
        await session.execute(text("PRAGMA foreign_keys=OFF"))
        await session.execute(text("DELETE FROM physical_nodes"))
        await session.commit()
        await session.execute(text("PRAGMA foreign_keys=ON"))
    from app.core.errors import Range42Error

    async with factory() as session:
        with pytest.raises(Range42Error) as error:
            await observe(mod, session, "alias")
        assert error.value.code == "PHYSICAL_NODE_IDENTITY_CONFLICT"


@pytest.mark.asyncio
async def test_changed_binding_observation_does_not_become_a_new_authority(registry):
    from sqlalchemy import text

    mod, factory = registry
    async with factory() as session:
        reviewed = await observe(mod, session)
        await mod.save_observation(
            session,
            reviewed,
            review_revision=reviewed.revision,
            actor="fixture-director",
        )
    async with factory() as session:
        await session.execute(
            text("UPDATE physical_node_bindings SET observation_json=:value"),
            {
                "value": mod.NodeAttestation(
                    **proof(hardware_binding="9" * 64)
                ).model_dump_json()
            },
        )
        await session.commit()
    from app.core.errors import Range42Error

    async with factory() as session:
        with pytest.raises(Range42Error) as error:
            await observe(mod, session, "alias")
        assert error.value.code == "PHYSICAL_NODE_IDENTITY_CONFLICT"


@pytest.mark.asyncio
async def test_lost_reply_recovery_cannot_bypass_retained_registry_corruption(registry):
    from sqlalchemy import text
    from app.core.errors import Range42Error

    mod, factory = registry
    async with factory() as session:
        reviewed = await observe(mod, session)
        await mod.save_observation(
            session,
            reviewed,
            review_revision=reviewed.revision,
            actor="fixture-director",
        )
    async with factory() as session:
        await session.execute(
            text("UPDATE physical_node_bindings SET observation_json=:value"),
            {
                "value": mod.NodeAttestation(
                    **proof(hardware_binding="9" * 64)
                ).model_dump_json()
            },
        )
        await session.commit()
    async with factory() as session:
        with pytest.raises(Range42Error) as error:
            await mod.save_observation(
                session,
                reviewed,
                review_revision=reviewed.revision,
                actor="fixture-director",
            )
        assert error.value.code == "PHYSICAL_NODE_IDENTITY_CONFLICT"
