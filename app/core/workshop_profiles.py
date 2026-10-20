"""Operator-approved provisioning bounds, separate from browser run inputs."""
from ipaddress import IPv4Address, IPv4Network

from pydantic import Field, model_validator

from app.core.errors import Range42Error
from app.core.workshop_runs import StrictModel


class WorkshopNetwork(StrictModel):
    bridge: str = Field(pattern=r'^vmbr[0-9]{1,11}$')
    subnet: str = Field(max_length=18)
    gateway: str = Field(max_length=15)

    @model_validator(mode='after')
    def addresses(self):
        network = IPv4Network(self.subnet, strict=True)
        if network.prefixlen != 28 or IPv4Address(self.gateway) != network[1] or not any(
                network.subnet_of(IPv4Network(pool)) for pool in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')):
            raise ValueError('Approved participant networks require private /28s with their first host as gateway')
        return self


class InfrastructureNic(StrictModel):
    interface: str = Field(pattern=r'^net(?:[0-9]|[12][0-9]|3[01])$')
    bridge: str = Field(pattern=r'^vmbr[0-9]{1,11}$')
    mac: str = Field(pattern=r'^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$')


class InfrastructureGuest(StrictModel):
    vmid: int = Field(ge=100, le=999999999)
    node: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')
    vmgenid: str = Field(pattern=r'^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$')
    owner: str = Field(min_length=1, max_length=256)
    nics: list[InfrastructureNic] = Field(min_length=1, max_length=32)

    @model_validator(mode='after')
    def exact_identity(self):
        if any(ord(char) < 32 for char in self.owner) or len({nic.interface for nic in self.nics}) != len(self.nics):
            raise ValueError('Infrastructure identities and NICs must be exact and unique')
        return self


class WorkshopProfile(StrictModel):
    version: int = Field(ge=1, le=1)
    id: str = Field(pattern=r'^[a-z][a-z0-9-]{0,63}$')
    vmid_ranges: list[list[int]] = Field(min_length=1, max_length=16)
    networks: list[WorkshopNetwork] = Field(min_length=1, max_length=64)
    proxmox_template: int = Field(ge=100, le=999999999)
    deployer_template: int = Field(ge=100, le=999999999)
    storage: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_.-]{0,63}$')
    infrastructure_guests: list[InfrastructureGuest] = Field(default_factory=list, max_length=32)

    @model_validator(mode='after')
    def bounds(self):
        ranges = sorted(self.vmid_ranges)
        if any(len(pair) != 2 or not 1000 <= pair[0] <= pair[1] <= 999999999 for pair in ranges):
            raise ValueError('Invalid approved guest VMID ranges')
        if sum(end - start + 1 for start, end in ranges) > 4096 or any(
                left[1] >= right[0] for left, right in zip(ranges, ranges[1:])):
            raise ValueError('Approved guest ranges must be bounded and disjoint')
        bridges = {net.bridge for net in self.networks}
        subnets = [IPv4Network(net.subnet) for net in self.networks]
        if len(bridges) != len(self.networks) or any(a.overlaps(b) for i, a in enumerate(subnets) for b in subnets[i + 1:]):
            raise ValueError('Approved bridges and subnets must be unique')
        reserved = [self.proxmox_template, self.deployer_template, *(guest.vmid for guest in self.infrastructure_guests)]
        if len(set(reserved)) != len(reserved) or any(start <= vmid <= end for start, end in ranges for vmid in reserved):
            raise ValueError('Templates and infrastructure must be separate from run guest ranges')
        if any(nic.bridge not in bridges for guest in self.infrastructure_guests for nic in guest.nics):
            raise ValueError('Infrastructure exceptions must name an approved participant bridge')
        return self

    def public(self):
        return self.model_dump(exclude={'infrastructure_guests'})


def require_workshop_profile(context, plan: dict):
    profile = context.workshop_profile
    if profile is None:
        raise Range42Error(status=409, code='WORKSHOP_PROVISIONING_PROFILE_REQUIRED',
            message='This environment has no approved workshop provisioning profile. Prepare and register a dedicated profile before creating a run.')
    networks = {net.bridge: net for net in profile.networks}
    valid = (plan['proxmox_template'] == profile.proxmox_template
        and plan['deployer_template'] == profile.deployer_template and plan['storage'] == profile.storage
        and all(any(start <= guest['vm_id'] <= end for start, end in profile.vmid_ranges) for guest in plan['guests'])
        and all(net['vnet'] in networks and (net['subnet'], net['gateway']) ==
            (networks[net['vnet']].subnet, networks[net['vnet']].gateway) for net in plan['networks']))
    if not valid:
        raise Range42Error(status=409, code='WORKSHOP_PROVISIONING_SCOPE',
            message='Choose VM IDs, participant networks, source templates and storage within this environment’s approved workshop profile.')
    return profile
