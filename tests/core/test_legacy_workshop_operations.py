"""Legacy seat actions must use the canonical parent, never overlapping children."""

import pytest
from sqlalchemy import select

from tests.core import test_workshop_ownership_import as ownership_tests
from app.core.models import Deployment, Attempt
from app.core.errors import Range42Error

env = ownership_tests.env


@pytest.mark.asyncio
async def test_parent_owns_the_exact_instance_operation_and_retained_source(
    env, monkeypatch
):
    from app.core import legacy_workshop_operations as legacy
    from app.routes.v1.deployments.attempts import reserve_attempt
    from app.schemas.v1.deployments import AttemptCreate

    mod, factory, _, _ = env
    await ownership_tests.save(env, await ownership_tests.observe(env))
    monkeypatch.setenv("RANGE42_AUTO_START_ATTEMPTS", "0")
    async with factory() as session:
        operation = await legacy.operation(session, "cohort", "user01", "reset")
        assert (
            operation["seat"] == 1
            and operation["instance_id"] == "user01"
            and operation["kind"] == "legacy_workshop"
        )
        result = await reserve_attempt(
            "cohort",
            AttemptCreate(scope="reset", confirm_codename="COHORT"),
            session,
            operation=operation,
        )
        assert result.deployment_id == "cohort" and result.scope == "reset"
    async with factory() as session:
        assert (await session.get(Deployment, "cohort")).native["parameters"][
            "WORKSHOP_ONLY_SEAT"
        ] == 0
        assert (await session.scalar(select(Attempt))).operation == operation


@pytest.mark.asyncio
async def test_unimported_or_unscoped_legacy_work_cannot_resurrect_guests(
    env, monkeypatch
):
    from app.core import legacy_workshop_operations as legacy
    from app.routes.v1.deployments.attempts import reserve_attempt
    from app.schemas.v1.deployments import AttemptCreate

    _, factory, _, _ = env
    monkeypatch.setenv("RANGE42_AUTO_START_ATTEMPTS", "0")
    async with factory() as session:
        with pytest.raises(Range42Error):
            await legacy.operation(session, "cohort", "user01", "reset")
        with pytest.raises(Range42Error) as error:
            await reserve_attempt("cohort", AttemptCreate(scope="full"), session)
        assert error.value.code == "WORKSHOP_PARENT_OWNERSHIP_REQUIRED"
    await ownership_tests.save(env, await ownership_tests.observe(env))
    async with factory() as session:
        for username in ["user06", "user11", "../user01"]:
            with pytest.raises(Range42Error):
                await legacy.operation(session, "cohort", username, "reset")


@pytest.mark.asyncio
async def test_source_parameter_override_is_derived_from_the_retained_instance(env):
    from app.core import legacy_workshop_operations as legacy

    _, factory, _, _ = env
    await ownership_tests.save(env, await ownership_tests.observe(env))
    async with factory() as session:
        dep = await session.get(Deployment, "cohort")
        op = await legacy.operation(session, "cohort", "user02", "configure")
        params = await legacy.parameters(session, dep, op)
        assert params["WORKSHOP_ONLY_SEAT"] == 2
        assert dep.native["parameters"]["WORKSHOP_ONLY_SEAT"] == 0
        for invalid in [
            {**op, "seat": 1},
            {**op, "import_id": "f" * 32},
            {**op, "variables": {"WORKSHOP_ONLY_SEAT": 0}},
        ]:
            with pytest.raises(Range42Error):
                await legacy.parameters(session, dep, invalid)
