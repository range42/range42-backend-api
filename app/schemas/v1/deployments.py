"""Deployment lifecycle + snapshot/rollback/timings DTOs."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator


class NativeDeployment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    context_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    path: str = Field(min_length=1, max_length=1024)
    features: dict[str, StrictBool] = Field(default_factory=dict, max_length=128)
    parameters: dict = Field(default_factory=dict, max_length=64)

    @field_validator("path")
    @classmethod
    def relative_path(cls, value):
        if value.startswith("/") or "\\" in value or any(ord(c) < 32 for c in value) or any(p in {"", ".", "..", ".git"} for p in value.split("/")):
            raise ValueError("Scenario path must stay inside the saved repository")
        return value


class DeploymentCreate(BaseModel):
    codename: str = Field(pattern=r"^[A-Z][A-Z0-9_-]{1,31}$")
    scenario_label: str = Field(min_length=1, max_length=128)
    project_id: str
    target_host_id: str
    team_count: int = Field(ge=1, le=64)
    catalog_sha: str | None = None
    project_sha: str | None = None
    allocation_reservation_id: str | None = Field(default=None, min_length=1, max_length=64)
    secrets: dict[str, str] | None = None
    native: NativeDeployment | None = None

    @model_validator(mode="after")
    def native_revision(self):
        import re
        if self.native and self.team_count != 1:
            raise ValueError("Native deployment uses the saved scenario topology; team_count must be 1")
        if self.native and (not self.project_sha or not re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", self.project_sha)
                            or self.allocation_reservation_id or self.secrets):
            raise ValueError("Native deployment requires a saved commit and uses the selected context's credentials and allocations")
        return self

    @field_validator("scenario_label")
    @classmethod
    def concrete_scenario(cls, value: str) -> str:
        if value == "_universal":
            raise ValueError("_universal is retired; save a concrete scenario and use its name")
        return value


class DeploymentOut(BaseModel):
    id: str
    codename: str
    scenario_label: str
    project_id: str
    target_host_id: str
    state: str
    current_attempt_id: str | None = None
    team_count: int
    workspace_path: str
    created_at: datetime
    updated_at: datetime
    catalog_sha: str | None = None
    project_sha: str | None = None
    effective_doc_hash: str | None = None
    native: dict | None = None


class AttemptOut(BaseModel):
    id: str
    deployment_id: str
    scope: str
    project_sha: str | None = None
    operation: dict | None = None
    operation_result: dict | None = None
    team_id: int | None = None
    state: str
    sub_reason: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    rc: int | None = None
    event_cursor_tip: int


class DeploymentAllocationOut(BaseModel):
    deployment_id: str
    project_sha: str
    host_id: str
    node_name: str
    assignments: list[dict]
    created_at: datetime


class AttemptCreate(BaseModel):
    scope: str = Field(pattern=r"^(full|configure|failed_teams|team_reset|teardown|rollback_all|rollback_team|deploy_vms|delete_vms|reset|deploy_networks|delete_networks)$")
    team_id: int | None = None
    confirm_codename: str | None = None
    project_sha: str | None = Field(default=None, pattern=r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")


class PreflightResponse(BaseModel):
    id: str
    deployment_id: str
    attempt_id: str | None = None
    ts: datetime
    result: str
    checks: list[dict]


class PreflightRequest(BaseModel):
    scope: str = Field(default="full", pattern=r"^(full|configure|teardown|deploy_vms|delete_vms|reset|deploy_networks|delete_networks)$")
    project_sha: str | None = Field(default=None, pattern=r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")


class TeardownRequest(BaseModel):
    confirm_codename: str


class SnapshotCreate(BaseModel):
    scope: str = Field(pattern=r"^(team|all|shared)$")
    team_id: int | None = None
    name: str | None = None


class SnapshotOut(BaseModel):
    id: str
    deployment_id: str
    vm_id: int
    team_id: int | None = None
    name: str
    kind: str
    created_at: datetime
    expired: bool


class RollbackRequest(BaseModel):
    scope: str = Field(pattern=r"^(team|all|shared)$")
    team_id: int | None = None
    snapshot_id: str | None = None


class TimingsRow(BaseModel):
    stage: str
    team_id: int | None = None
    duration_ms: int
    start_ts: datetime
    end_ts: datetime


class TimingsResponse(BaseModel):
    deployment_id: str
    rows: list[TimingsRow]
