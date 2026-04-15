"""Deployment lifecycle + snapshot/rollback/timings DTOs."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class DeploymentCreate(BaseModel):
    codename: str = Field(pattern=r"^[A-Z][A-Z0-9_-]{1,31}$")
    scenario_label: str = Field(min_length=1, max_length=128)
    project_id: str
    target_host_id: str
    team_count: int = Field(ge=1, le=64)
    catalog_sha: str | None = None
    project_sha: str | None = None
    secrets: dict[str, str] | None = None


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


class AttemptOut(BaseModel):
    id: str
    deployment_id: str
    scope: str
    team_id: int | None = None
    state: str
    sub_reason: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    rc: int | None = None
    event_cursor_tip: int


class AttemptCreate(BaseModel):
    scope: str = Field(pattern=r"^(full|failed_teams|team_reset|teardown|rollback_all|rollback_team)$")
    team_id: int | None = None


class PreflightResponse(BaseModel):
    id: str
    deployment_id: str
    attempt_id: str | None = None
    ts: datetime
    result: str
    checks: list[dict]


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
