from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.physical_capacity import CapacityObservation


class CapacitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    configured_memory_mib: int = Field(ge=0)
    memory_headroom_mib: int
    configured_vcpus: int = Field(ge=0)
    vcpu_headroom: int
    resumable_guests: int = Field(ge=0)
    allocation_granted: Literal[False]
    execution_ready: Literal[False]
    infrastructure_qualified: Literal[False]


class CapacityReview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    target_host_id: str
    observed_at: str
    observation: CapacityObservation
    summary: CapacitySummary
