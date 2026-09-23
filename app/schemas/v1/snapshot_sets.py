"""Explicit snapshot review and member outcome DTOs; no upstream config bytes."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SnapshotSetPlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="Snapshot set", min_length=1, max_length=128)
    description: str = Field(default="", max_length=1024)
    vmstate: Literal[False] = False


class SnapshotSetExecuteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class SnapshotProof(BaseModel):
    snaptime: int = Field(ge=1)
    config_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class SnapshotObservedMember(BaseModel):
    vm_id: int = Field(ge=100)
    vm_name: str
    uuid: str
    config_digest: str
    restore_digest: str
    status: Literal["running", "stopped"]


class SnapshotOutcome(BaseModel):
    vm_id: int
    state: Literal[
        "planned",
        "dispatching",
        "accepted",
        "unconfirmed",
        "not_started",
        "failed",
        "succeeded",
    ]
    upid: str | None = None
    dispatched_at: str | None = None
    code: str | None = None
    snapshot: SnapshotProof | None = None


class SnapshotMember(SnapshotObservedMember):
    state: str | None = None
    upid: str | None = None
    dispatched_at: str | None = None
    code: str | None = None
    snapshot: SnapshotProof | None = None


class SnapshotRetentionPolicy(BaseModel):
    keep_count: int
    keep_days: int


class SnapshotOperationOut(BaseModel):
    id: str
    kind: Literal["create", "rollback", "delete"]
    state: Literal[
        "planned",
        "running",
        "needs_review",
        "succeeded",
        "partial",
        "failed",
        "cancelled",
        "superseded",
    ]
    recovery: Literal["operator_required", "poll_saved_tasks", "none"]
    plan_digest: str
    expires_at: str
    members: list[SnapshotOutcome]
    reviewed_members: list[SnapshotObservedMember]
    attempt_id: str | None
    retention: SnapshotRetentionPolicy | None


class SnapshotSetOut(BaseModel):
    id: str
    deployment_id: str
    name: str
    description: str
    state: Literal[
        "planned",
        "creating",
        "rolling_back",
        "deleting",
        "needs_review",
        "complete",
        "partial",
        "failed",
        "deleted",
    ]
    project_sha: str
    host_id: str
    target_digest: str
    native_name: str
    atomic: Literal[False]
    vmstate: Literal[False]
    created_at: str
    members: list[SnapshotMember]
    operation: SnapshotOperationOut
    operations: list[SnapshotOperationOut]


class SnapshotRetentionReview(BaseModel):
    policy: SnapshotRetentionPolicy
    automatic_enforcement: Literal[False]
    eligible_count: int
    candidates: list[SnapshotSetOut]
