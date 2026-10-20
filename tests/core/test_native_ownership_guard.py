from importlib.util import find_spec

import pytest

from tests.core import test_workshop_ownership_import as ownership_tests
from app.core.errors import Range42Error
from app.core.allocation_models import DeploymentAllocation

env = ownership_tests.env


def test_native_dispatch_checks_canonical_resource_ownership():
    assert find_spec("app.core.native_ownership_guard"), (
        "Native dispatch needs a canonical ownership guard"
    )


@pytest.mark.asyncio
async def test_old_metadata_cannot_mutate_another_parents_vm_ids(env):
    from app.core.native_ownership_guard import assert_native_scope_free

    _, factory, _, _ = env
    await ownership_tests.save(env, await ownership_tests.observe(env))
    async with factory() as session:
        with pytest.raises(Range42Error) as error:
            await assert_native_scope_free(session, "old-child", [43010])
        assert error.value.code == "ALLOCATION_OCCUPIED"
        await assert_native_scope_free(session, "cohort", [43010])
        await assert_native_scope_free(session, "other", [55000])


@pytest.mark.asyncio
async def test_corrupt_retained_ownership_is_not_treated_as_unoccupied(env):
    from app.core.native_ownership_guard import assert_native_scope_free

    _, factory, _, _ = env
    saved = await ownership_tests.save(env, await ownership_tests.observe(env))
    async with factory() as session:
        claim = await session.get(DeploymentAllocation, saved["allocation_id"])
        claim.assignments = [{"vm_id": True}]
        await session.commit()
    async with factory() as session:
        with pytest.raises(Range42Error) as error:
            await assert_native_scope_free(session, "other", [55000])
        assert error.value.code == "ALLOCATION_REVIEW_REQUIRED"
