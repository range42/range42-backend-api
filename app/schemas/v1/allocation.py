"""Bounded, strictly typed authoring allocation requests and leases."""
from __future__ import annotations

import ipaddress
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

Key = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")]
Bridge = Annotated[str, Field(min_length=1, max_length=15, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")]
Vmid = Annotated[int, Field(ge=100, le=999999999)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AllocationNetwork(StrictModel):
    network_id: Key
    bridge: Bridge
    subnet: str = Field(max_length=18)
    gateway: str | None = Field(default=None, max_length=15)
    reserved_ips: list[str] = Field(default_factory=list, max_length=256)

    @model_validator(mode="after")
    def validate_subnet(self):
        network = ipaddress.IPv4Network(self.subnet, strict=True)
        if not 16 <= network.prefixlen <= 30:
            raise ValueError("Allocation supports IPv4 subnets with prefixes 16 through 30")
        for value in [*self.reserved_ips, *([self.gateway] if self.gateway else [])]:
            address = ipaddress.IPv4Address(value)
            if address not in network or address in (network.network_address, network.broadcast_address):
                raise ValueError("Gateway and reserved addresses must be usable addresses in the subnet")
        return self


class AllocationNic(StrictModel):
    index: int = Field(ge=0, le=31)
    network_id: Key
    ip: str | None = Field(default=None, max_length=15)


class AllocationVm(StrictModel):
    node_id: Key
    vm_id: Vmid | None = None
    nics: list[AllocationNic] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def contiguous_nics(self):
        if sorted(nic.index for nic in self.nics) != list(range(len(self.nics))):
            raise ValueError("NIC indexes must be unique and contiguous, beginning at zero")
        return self


class AllocationRequest(StrictModel):
    project_key: Key
    lease_seconds: int = Field(default=3600, ge=300, le=86400)
    vmid_start: Vmid = 2000
    vmid_end: Vmid = 8999
    networks: list[AllocationNetwork] = Field(min_length=1, max_length=32)
    vms: list[AllocationVm] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def coherent_plan(self):
        if self.vmid_end < self.vmid_start:
            raise ValueError("VMID range end precedes its start")
        networks = {network.network_id: network for network in self.networks}
        if len(networks) != len(self.networks):
            raise ValueError("Network IDs must be unique")
        subnets = [ipaddress.IPv4Network(network.subnet) for network in self.networks]
        if any(a.overlaps(b) for i, a in enumerate(subnets) for b in subnets[i + 1:]):
            raise ValueError("Declared allocation subnets must not overlap")
        if len({vm.node_id for vm in self.vms}) != len(self.vms):
            raise ValueError("VM node IDs must be unique")
        if sum(len(vm.nics) for vm in self.vms) > 256:
            raise ValueError("At most 256 NICs can be reserved in one project")
        for vm in self.vms:
            for nic in vm.nics:
                if nic.network_id not in networks:
                    raise ValueError("Each NIC must reference a declared network")
                if nic.ip is not None:
                    ipaddress.IPv4Address(nic.ip)
        return self


class AllocatedNic(StrictModel):
    index: int
    network_id: str
    bridge: str
    subnet: str
    ip: str
    prefix: int
    gateway: str | None = None


class AllocatedVm(StrictModel):
    node_id: str
    vm_id: int
    nics: list[AllocatedNic]


class AllocationLease(BaseModel):
    reservation_id: str
    project_key: str
    host_id: str
    node_name: str
    expires_at: datetime
    checked_at: datetime
    assignments: list[AllocatedVm]
    limitations: list[str]
