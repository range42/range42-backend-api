from typing import Literal

from pydantic import Field

from app.core.capacity_accounting import HeldGuest
from app.core.physical_capacity import Digest, PhysicalResources, StrictObservation


class ReviewCapacity(StrictObservation):
    pass


class ReserveCapacity(StrictObservation):
    review_revision: Digest


class PoolBudget(StrictObservation):
    cluster_id: Digest
    replicas: int = Field(ge=1, le=64)
    available_bytes: int = Field(ge=0)
    potential_bytes: int = Field(ge=0)
    held_bytes: int = Field(ge=0)
    logical_headroom_bytes: int


class ClusterBudget(StrictObservation):
    available_bytes: int = Field(ge=0)
    potential_bytes: int = Field(ge=0)
    held_bytes: int = Field(ge=0)
    raw_headroom_bytes: int


class CapacityAssessment(StrictObservation):
    admissible: bool
    memory_headroom_mib: int
    vcpu_headroom: int
    pools: dict[Digest, PoolBudget]
    clusters: dict[Digest, ClusterBudget]
    blocking_codes: list[
        Literal["MEMORY_COMMITTED", "CPU_BUDGET_COMMITTED", "SHARED_STORAGE_COMMITTED"]
    ]
    allocation_granted: Literal[False]


class CapacityHoldOut(StrictObservation):
    id: str
    allocation_id: str
    physical_node_id: str
    status: Literal["pending_root", "active", "releasing", "retired"]
    revision: int = Field(ge=1)
    approval_revision: Digest
    approved_by: str
    created_at: str
    requirements: list[HeldGuest]
    reservation_recorded: Literal[True]
    root_accepted: Literal[False]
    native_request_may_have_been_sent: bool = False
    unused_retirement_review_available: bool = False
    retired_by: str | None = None
    retired_at: str | None = None


class CapacityHoldStatus(StrictObservation):
    deployment_id: str
    applicable: bool
    hold: CapacityHoldOut | None
    live_verified: Literal[False]
    execution_ready: Literal[False]
    infrastructure_qualified: Literal[False]


class CapacityHoldReview(StrictObservation):
    deployment_id: str
    review_revision: Digest
    physical_node_id: str
    node_name: str
    observed_at: str
    requirements: list[HeldGuest]
    resources: PhysicalResources
    assessment: CapacityAssessment
    execution_ready: Literal[False]
    infrastructure_qualified: Literal[False]
    unused_retirement_eligible: bool = False
