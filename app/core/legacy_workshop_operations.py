"""Compatibility for the installed legacy controller; new runs use native plans.

Legacy per-seat work executes on the imported canonical parent. Its original
pinned recipe receives only the server-derived seat parameter; clients never
supply runtime variables or overlapping child allocations.
"""

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from app.core import workshop_ownership as ownership
from app.core.errors import Range42Error


class LegacyOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["legacy_workshop"]
    instance_id: str = Field(pattern=r"^user(?:0[1-9]|10)$")
    seat: int = Field(ge=1, le=10)
    import_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    action: Literal["deploy", "configure", "reset", "teardown"]


def is_legacy(deployment):
    native = deployment.native or {}
    return native.get("path") == "scenarios/range42-native-workshop" and not native.get(
        "workshop_plan"
    )


def refused():
    return Range42Error(
        status=409,
        code="WORKSHOP_PARENT_OWNERSHIP_REQUIRED",
        message="Use the acknowledged canonical parent and its selected participant action. Preserve the original ownership handoff before recovery.",
    )


async def operation(session, deployment_id, instance_id, action):
    dep, _, _ = await ownership.binding(session, deployment_id)
    row = await ownership._record(session, deployment_id)
    if not is_legacy(dep) or row is None or row.phase != "active":
        raise refused()
    witness, _ = await ownership._valid_record(session, row)
    if len([vm for vm in witness.assignments if vm.instance_id == instance_id]) != 2:
        raise refused()
    try:
        op = LegacyOperation(
            kind="legacy_workshop",
            instance_id=instance_id,
            seat=int(instance_id[4:]),
            import_id=row.id,
            action=action,
        )
    except (ValueError, TypeError):
        raise refused() from None
    return op.model_dump()


async def parameters(session, deployment, selected, *, scope=None):
    try:
        op = LegacyOperation.model_validate(selected)
        fresh = await operation(session, deployment.id, op.instance_id, op.action)
        if fresh != op.model_dump() or (
            scope is not None
            and scope != ("full" if op.action == "deploy" else op.action)
        ):
            raise ValueError()
    except (ValueError, TypeError):
        raise refused() from None
    return {**deployment.native.get("parameters", {}), "WORKSHOP_ONLY_SEAT": op.seat}
