"""Closed administrator reviews for the original legacy guest generations."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.workshop_ownership import OwnershipWitness


class InspectOwnership(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SaveOwnership(InspectOwnership):
    review_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class OwnershipReview(BaseModel):
    deployment_id: str
    revision: str
    witness: OwnershipWitness
    capacity_borrowing_allowed: Literal[False]
    remote_quiescence_verified: Literal[False]


class OwnershipStatus(BaseModel):
    deployment_id: str
    phase: Literal[
        "unimported", "pending_controller", "claim_saved", "active", "released"
    ]
    import_id: str | None
    allocation_id: str | None
    ownership_revision: str | None
    project_sha: str | None
    target_host_id: str | None
    assignments: list[dict]
    resume_review_revision: str | None
    changed_by: str | None
    capacity_borrowing_allowed: Literal[False]
    remote_quiescence_verified: Literal[False]
