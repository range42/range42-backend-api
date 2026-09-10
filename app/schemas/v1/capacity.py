"""Measured host capacity; null denotes data unavailable to this API token."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CapacityIssue(BaseModel):
    code: str
    resource: str
    message: str


class CpuCapacity(BaseModel):
    logical_cpus: int | None = None
    utilization: float | None = None


class MemoryCapacity(BaseModel):
    total_bytes: int | None = None
    used_bytes: int | None = None
    free_bytes: int | None = None


class StorageCapacity(MemoryCapacity):
    storage: str
    type: str | None = None
    content: list[str] = Field(default_factory=list)
    enabled: bool | None = None
    active: bool | None = None
    shared: bool | None = None


class HostCapacity(BaseModel):
    host_id: str
    node_name: str
    observed_at: datetime
    status: Literal["available", "partial", "unavailable"]
    cpu: CpuCapacity
    memory: MemoryCapacity
    storage: list[StorageCapacity]
    issues: list[CapacityIssue]
    limitations: list[str]
