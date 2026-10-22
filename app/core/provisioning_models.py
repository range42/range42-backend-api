"""Strict provider-independent resource and gateway identities."""

from ipaddress import IPv4Address, IPv4Network
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class GatewayManagement(StrictModel):
    management_bridge: str = Field(pattern=r"^vmbr[1-9][0-9]{2,10}$")
    management_subnet: str = Field(max_length=18)

    @model_validator(mode="after")
    def private_management(self):
        network = IPv4Network(self.management_subnet, strict=True)
        if network.prefixlen != 29 or not any(
            network.subnet_of(IPv4Network(pool))
            for pool in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
        ):
            raise ValueError("Gateway management requires a separate private /29")
        return self


class ManagedGateway(GatewayManagement):
    vm_id: int = Field(ge=1000, le=999999999)
    management_ip: str = Field(max_length=15)

    @model_validator(mode="after")
    def allocated_address(self):
        network = IPv4Network(self.management_subnet)
        address = IPv4Address(self.management_ip)
        if address not in network or address in (network[0], network[1], network[-1]):
            raise ValueError("Reserve the first management host for the physical node")
        return self
