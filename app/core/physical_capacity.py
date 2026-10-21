"""Strict, read-only native resource observations. No reservation is granted."""

from typing import Literal, Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.physical_nodes import NodeAttestation
from app.core.preparation_transport import read_agent, unavailable

BoundedInt = Annotated[int, Field(ge=0, le=2**63 - 1)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class StrictObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class GuestResources(StrictObservation):
    vm_id: int = Field(ge=100, le=999999999)
    kind: Literal["qemu", "lxc"]
    state: Literal["running", "stopped", "paused", "suspended"]
    template: bool
    vmgenid: (
        Annotated[
            str,
            Field(
                pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
            ),
        ]
        | None
    )
    resumable: bool
    memory_mib: int = Field(ge=0, le=2**30)
    vcpus: int = Field(ge=0, le=65536)
    configuration_revision: Digest

    @model_validator(mode="after")
    def check_charge(self):
        if self.template:
            if (
                self.resumable
                or self.memory_mib
                or self.vcpus
                or self.state != "stopped"
            ):
                raise ValueError("Inconsistent template resource charge")
        elif not self.resumable or not self.memory_mib or not self.vcpus:
            raise ValueError("Resumable guests retain their configured resources")
        return self


class VolumeResources(StrictObservation):
    volume: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_./-]{0,255}$")
    size_bytes: int = Field(ge=1, le=2**63 - 1)


class StorageResources(StrictObservation):
    storage: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    namespace: str = Field(pattern=r"^(?:[A-Za-z0-9][A-Za-z0-9_.-]{0,63})?$")
    pool_id: Digest
    cluster_id: Digest
    rados_pool_id: int = Field(ge=1, le=2**31 - 1)
    replicas: int = Field(ge=1, le=64)
    logical_available_bytes: BoundedInt
    cluster_raw_available_bytes: BoundedInt
    potential_bytes: BoundedInt
    volumes: list[VolumeResources] = Field(max_length=4096)

    @model_validator(mode="after")
    def check_volumes(self):
        if (
            len({row.volume for row in self.volumes}) != len(self.volumes)
            or sum(row.size_bytes for row in self.volumes) != self.potential_bytes
        ):
            raise ValueError("Incomplete or ambiguous volume accounting")
        return self


class PhysicalResources(StrictObservation):
    logical_cpus: int = Field(ge=1, le=65536)
    memory_total_mib: int = Field(ge=1, le=2**30)
    max_vcpus: int = Field(ge=1, le=65536)
    memory_reserve_mib: int = Field(ge=512, le=2**30)
    guests: list[GuestResources] = Field(max_length=1024)
    storage: list[StorageResources] = Field(min_length=1, max_length=16)
    execution_ready: Literal[False]
    infrastructure_qualified: Literal[False]

    @field_validator("execution_ready", "infrastructure_qualified", mode="before")
    @classmethod
    def false_readiness(cls, value):
        if value is not False:
            raise ValueError("Resource observation cannot grant execution")
        return value

    @model_validator(mode="after")
    def check_inventory(self):
        if len({row.vm_id for row in self.guests}) != len(self.guests):
            raise ValueError("Duplicate guest identity")
        if len({row.storage for row in self.storage}) != len(self.storage):
            raise ValueError("Duplicate storage alias")
        if self.memory_reserve_mib >= self.memory_total_mib:
            raise ValueError("Host reserve exhausts physical memory")
        return self


class CapacityObservation(StrictObservation):
    protocol: Literal["range42-capacity-observation-v1"]
    node: NodeAttestation
    resources: PhysicalResources

    @model_validator(mode="after")
    def check_capability(self):
        if 'capacity' not in self.node.supported_operations:
            raise ValueError("Resource inspection is not explicitly enabled")
        return self


async def inspect_capacity(configuration):
    try:
        value = await read_agent(
            configuration, action="capacity", maximum=4194304, timeout=110
        )
        observed = CapacityObservation.model_validate(value)
        if (observed.node.installation_id, observed.node.node_name) != (
            configuration.installation_id,
            configuration.node_name,
        ):
            raise ValueError("Resource observation belongs to another installation")
        return observed
    except (ValueError, TypeError):
        raise unavailable() from None


def summarize(observed):
    resources = observed.resources
    memory = sum(row.memory_mib for row in resources.guests)
    cpus = sum(row.vcpus for row in resources.guests)
    return {
        "configured_memory_mib": memory,
        "memory_headroom_mib": resources.memory_total_mib
        - resources.memory_reserve_mib
        - memory,
        "configured_vcpus": cpus,
        "vcpu_headroom": resources.max_vcpus - cpus,
        "resumable_guests": sum(row.resumable for row in resources.guests),
        "allocation_granted": False,
        "execution_ready": False,
        "infrastructure_qualified": False,
    }
