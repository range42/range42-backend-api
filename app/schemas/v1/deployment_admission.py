from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DeploymentAdmissionChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["pause", "resume"]
    expected_revision: int = Field(ge=0, lt=2**53 - 1)
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    pause_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    confirmation: Literal["RESUME"] | None = None

    @model_validator(mode="after")
    def proof(self):
        if self.action == "pause" and (
            self.pause_id is not None or self.confirmation is not None
        ):
            raise ValueError("A new pause cannot reuse resume proof")
        if self.action == "resume" and (
            not self.pause_id or self.confirmation != "RESUME"
        ):
            raise ValueError("Review the exact pause and explicitly confirm RESUME")
        return self


class DeploymentAdmissionState(BaseModel):
    scope: Literal["managed_deployment_writers"]
    deployment_id: str
    available: bool
    state: Literal["open", "draining", "closed"]
    revision: int
    pause_id: str | None
    vm_ids: list[int]
    active_attempts: int
    held_workspace_locks: int
    active_snapshot_operations: int
    changed_by: str | None
    changed_at: datetime | None
    remote_quiescence_verified: Literal[False]
    capacity_borrowing_allowed: Literal[False]
