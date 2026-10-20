"""Dispatch may not touch another canonical deployment's exclusive VM IDs."""

from sqlalchemy import select

from app.core.allocation_models import DeploymentAllocation
from app.core.errors import Range42Error


def review_required():
    return Range42Error(
        status=409,
        code="ALLOCATION_REVIEW_REQUIRED",
        message="Review and restore the retained native allocation before dispatch.",
    )


async def assert_native_scope_free(session, deployment_id, vmids):
    if not isinstance(vmids, list) or any(
        type(vmid) is not int or not 100 <= vmid <= 999999999 for vmid in vmids
    ):
        raise review_required()
    requested = set(vmids)
    claims = (
        await session.scalars(
            select(DeploymentAllocation).execution_options(populate_existing=True)
        )
    ).all()
    for claim in claims:
        try:
            if not isinstance(claim.assignments, list):
                raise ValueError()
            ids = [row["vm_id"] for row in claim.assignments]
            if any(
                type(vmid) is not int or not 100 <= vmid <= 999999999 for vmid in ids
            ):
                raise ValueError()
        except (TypeError, KeyError, ValueError):
            raise review_required() from None
        if claim.deployment_id != deployment_id and requested.intersection(ids):
            raise Range42Error(
                status=409,
                code="ALLOCATION_OCCUPIED",
                message="This native action references another canonical deployment’s resources. Use its scoped parent operation or a separate allocation.",
            )
