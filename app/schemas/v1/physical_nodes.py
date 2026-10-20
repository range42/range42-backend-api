from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from app.core.physical_nodes import NodeOperation


class InspectNode(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SavePhysicalNode(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    review_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class PhysicalNodeBindingOut(BaseModel):
    target_host_id: str
    physical_node_id: str
    node_name: str
    hardware_binding: str
    api_certificate_sha256: str
    node_certificate_sha256: str
    boot_id: str
    recorded_at: str
    approved_by: str
    binding_recorded: Literal[True]
    live_verified: Literal[False]
    execution_ready: Literal[False]
    infrastructure_qualified: Literal[False]
    alias_count: int = Field(ge=1)


class PhysicalNodeStatus(BaseModel):
    target_host_id: str
    configuration_state: Literal["unavailable", "enrolled"]
    binding: PhysicalNodeBindingOut | None = None
    needs_review: bool


class PhysicalNodeReview(BaseModel):
    revision: str
    target_host_id: str
    physical_node_id: str
    hardware_binding: str
    node_name: str
    api_certificate_sha256: str
    node_certificate_sha256: str
    boot_id: str
    configuration_revision: str
    supported_operations: list[NodeOperation]
    execution_ready: Literal[False]
    infrastructure_qualified: Literal[False]
