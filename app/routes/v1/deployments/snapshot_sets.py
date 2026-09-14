"""Reviewed native snapshot sets; reconciliation never dispatches mutations."""

from fastapi import APIRouter, Query, Response

from app.core import snapshot_sets
from app.schemas.v1.common import Page
from app.schemas.v1.snapshot_sets import (
    SnapshotSetExecuteIn,
    SnapshotSetPlanIn,
    SnapshotSetOut,
    SnapshotRetentionReview,
)

router = APIRouter()


@router.get(
    "/{deployment_id}/snapshot-sets",
    response_model=Page[SnapshotSetOut],
    response_model_exclude_none=True,
)
async def list_snapshot_sets(
    deployment_id: str,
    offset: int = Query(0, ge=0, le=100000),
    limit: int = Query(20, ge=1, le=100),
):
    return await snapshot_sets.list_sets(deployment_id, offset, limit)


@router.post(
    "/{deployment_id}/snapshot-sets/retention/plan",
    status_code=201,
    response_model=SnapshotRetentionReview,
    response_model_exclude_none=True,
)
async def plan_retention(deployment_id: str, limit: int = Query(20, ge=1, le=20)):
    return await snapshot_sets.plan_retention(deployment_id, limit)


@router.post(
    "/{deployment_id}/snapshot-sets/plan",
    status_code=201,
    response_model=SnapshotSetOut,
    response_model_exclude_none=True,
)
async def plan_snapshot_set(deployment_id: str, payload: SnapshotSetPlanIn):
    return await snapshot_sets.plan_create(deployment_id, payload)


@router.get(
    "/{deployment_id}/snapshot-sets/{set_id}",
    response_model=SnapshotSetOut,
    response_model_exclude_none=True,
)
async def get_snapshot_set(deployment_id: str, set_id: str):
    return await snapshot_sets.detail(deployment_id, set_id)


@router.post(
    "/{deployment_id}/snapshot-sets/{set_id}/execute",
    status_code=202,
    response_model=SnapshotSetOut,
    response_model_exclude_none=True,
)
async def execute_snapshot_set(
    deployment_id: str, set_id: str, payload: SnapshotSetExecuteIn
):
    return await snapshot_sets.execute(deployment_id, set_id, payload.plan_digest)


@router.post(
    "/{deployment_id}/snapshot-sets/{set_id}/reconcile",
    response_model=SnapshotSetOut,
    response_model_exclude_none=True,
)
async def reconcile_snapshot_set(deployment_id: str, set_id: str):
    return await snapshot_sets.reconcile(deployment_id, set_id)


@router.post(
    "/{deployment_id}/snapshot-sets/{set_id}/rollback/plan",
    status_code=201,
    response_model=SnapshotSetOut,
    response_model_exclude_none=True,
)
async def plan_rollback(deployment_id: str, set_id: str):
    return await snapshot_sets.plan_existing(deployment_id, set_id, "rollback")


@router.post(
    "/{deployment_id}/snapshot-sets/{set_id}/delete/plan",
    status_code=201,
    response_model=SnapshotSetOut,
    response_model_exclude_none=True,
)
async def plan_delete(deployment_id: str, set_id: str):
    return await snapshot_sets.plan_existing(deployment_id, set_id, "delete")


@router.delete(
    "/{deployment_id}/snapshot-sets/{set_id}/plans/{operation_id}", status_code=204
)
async def cancel_snapshot_plan(deployment_id: str, set_id: str, operation_id: str):
    await snapshot_sets.cancel_plan(deployment_id, set_id, operation_id)
    return Response(status_code=204)
