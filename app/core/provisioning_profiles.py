"""Operator-approved resource bounds shared by native scenarios and providers."""

from ipaddress import IPv4Network
from typing import Annotated
from pydantic import Field, model_validator
from app.core.provisioning_models import GatewayManagement, StrictModel
from ipaddress import IPv4Address


class ProvisioningNetwork(StrictModel):
    bridge: str = Field(pattern=r"^vmbr[0-9]{1,11}$")
    subnet: str = Field(max_length=18)
    gateway: str = Field(max_length=15)

    @model_validator(mode="after")
    def addresses(self):
        network = IPv4Network(self.subnet, strict=True)
        if (
            network.prefixlen != 28
            or IPv4Address(self.gateway) != network[1]
            or not any(
                network.subnet_of(IPv4Network(pool))
                for pool in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
            )
        ):
            raise ValueError(
                "Approved participant networks require private /28s with their first host as gateway"
            )
        return self


class InfrastructureNic(StrictModel):
    interface: str = Field(pattern=r"^net(?:[0-9]|[12][0-9]|3[01])$")
    bridge: str = Field(pattern=r"^vmbr[0-9]{1,11}$")
    mac: str = Field(pattern=r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$")


class InfrastructureGuest(StrictModel):
    vmid: int = Field(ge=100, le=999999999)
    node: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    vmgenid: str = Field(pattern=r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
    owner: str = Field(min_length=1, max_length=256)
    nics: list[InfrastructureNic] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def exact_identity(self):
        if any(ord(char) < 32 for char in self.owner) or len(
            {nic.interface for nic in self.nics}
        ) != len(self.nics):
            raise ValueError(
                "Infrastructure identities and NICs must be exact and unique"
            )
        return self


Role = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,31}$")]
TemplateId = Annotated[int, Field(ge=1000, le=999999999)]
DiskGiB = Annotated[int, Field(ge=1, le=65536)]


class TemplateCapability(StrictModel):
    vm_id: TemplateId
    memory_mib: int = Field(ge=128, le=4194304)
    cores: int = Field(ge=1, le=256)
    disks_gib: list[DiskGiB] = Field(min_length=1, max_length=2)


class ProvisioningProfileView(StrictModel):
    version: int = Field(ge=1, le=1)
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    vmid_ranges: list[list[int]] = Field(min_length=1, max_length=16)
    storage: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
    templates: dict[Role, TemplateCapability] = Field(min_length=1, max_length=16)
    networks: list[ProvisioningNetwork] = Field(default_factory=list, max_length=64)
    managed_gateway: GatewayManagement | None = None

    @model_validator(mode="after")
    def bounds(self):
        ranges = sorted(self.vmid_ranges)
        if any(
            len(pair) != 2 or not 1000 <= pair[0] <= pair[1] <= 999999999
            for pair in ranges
        ):
            raise ValueError("Invalid approved guest VMID ranges")
        if sum(end - start + 1 for start, end in ranges) > 4096 or any(
            a[1] >= b[0] for a, b in zip(ranges, ranges[1:])
        ):
            raise ValueError("Approved guest ranges must be bounded and disjoint")
        reserved = [template.vm_id for template in self.templates.values()] + [
            guest.vmid for guest in getattr(self, "infrastructure_guests", [])
        ]
        if len(set(reserved)) != len(reserved) or any(
            start <= vmid <= end for start, end in ranges for vmid in reserved
        ):
            raise ValueError(
                "Templates and infrastructure must be separate from guest ranges"
            )
        bridges = [net.bridge for net in self.networks]
        subnets = [IPv4Network(net.subnet) for net in self.networks]
        if len(set(bridges)) != len(bridges) or any(
            a.overlaps(b) for i, a in enumerate(subnets) for b in subnets[i + 1 :]
        ):
            raise ValueError("Approved bridges and subnets must be unique")
        if any(
            nic.bridge not in bridges
            for guest in getattr(self, "infrastructure_guests", [])
            for nic in guest.nics
        ):
            raise ValueError("Infrastructure exceptions must name an approved bridge")
        if gateway := self.managed_gateway:
            if (
                getattr(self, "infrastructure_guests", [])
                or gateway.management_bridge in bridges
                or any(
                    IPv4Network(gateway.management_subnet).overlaps(net)
                    for net in subnets
                )
            ):
                raise ValueError(
                    "Gateway management must be separate from deployment networks"
                )
        return self

    def public(self):
        value = self.model_dump(exclude={"infrastructure_guests"})
        if self.managed_gateway is None:
            value.pop("managed_gateway", None)
        return value


class ProvisioningProfile(ProvisioningProfileView):
    infrastructure_guests: list[InfrastructureGuest] = Field(
        default_factory=list, max_length=32
    )


def resource_profile(context):
    """Legacy records are projected without rewriting their retained digests."""
    if profile := getattr(context, "provisioning_profile", None):
        return profile
    old = getattr(context, "workshop_profile", None)
    if old is None:
        return None
    data = old.model_dump(exclude={"proxmox_template", "deployer_template"})
    data["templates"] = {
        "proxmox": {
            "vm_id": old.proxmox_template,
            "memory_mib": 16384,
            "cores": 4,
            "disks_gib": [32, 128],
        },
        "deployer": {
            "vm_id": old.deployer_template,
            "memory_mib": 4096,
            "cores": 2,
            "disks_gib": [32],
        },
    }
    return ProvisioningProfile.model_validate(data)
