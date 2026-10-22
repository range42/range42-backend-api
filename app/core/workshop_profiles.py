"""Operator-approved provisioning bounds, separate from browser run inputs."""
from ipaddress import IPv4Network

from pydantic import Field, model_serializer, model_validator

from app.core.errors import Range42Error
from app.core.provisioning_models import GatewayManagement, StrictModel
from app.core.provisioning_profiles import InfrastructureGuest, InfrastructureNic, ProvisioningNetwork as WorkshopNetwork  # noqa: F401


class WorkshopProfileView(StrictModel):
    version: int = Field(ge=1, le=1)
    id: str = Field(pattern=r'^[a-z][a-z0-9-]{0,63}$')
    vmid_ranges: list[list[int]] = Field(min_length=1, max_length=16)
    networks: list[WorkshopNetwork] = Field(min_length=1, max_length=64)
    proxmox_template: int = Field(ge=100, le=999999999)
    deployer_template: int = Field(ge=100, le=999999999)
    storage: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_.-]{0,63}$')
    managed_gateway: GatewayManagement | None = None

    @model_serializer(mode='wrap')
    def compatible_serialization(self, handler):
        value = handler(self)
        if self.managed_gateway is None:
            value.pop('managed_gateway', None)
        return value


class WorkshopProfile(WorkshopProfileView):
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
        if gateway := self.managed_gateway:
            management = IPv4Network(gateway.management_subnet)
            if (self.infrastructure_guests or gateway.management_bridge in bridges
                    or any(management.overlaps(subnet) for subnet in subnets)):
                raise ValueError('Gateway management must be separate from participant networks')
        return self

    def public(self):
        return self.model_dump(exclude={'infrastructure_guests'})


def installation_profile(context):
    """Recipe-specific view of general bounds; never rewrites a registration."""
    if original:=getattr(context,'workshop_profile',None):
        return original
    profile=getattr(context,'provisioning_profile',None)
    if profile is None or not profile.networks:
        return None
    expected={'proxmox':(16384,4,[32,128]),'deployer':(4096,2,[32])}
    if any(role not in profile.templates or
            (profile.templates[role].memory_mib,profile.templates[role].cores,profile.templates[role].disks_gib)!=hardware
            for role,hardware in expected.items()):
        return None
    return WorkshopProfile.model_validate({**profile.model_dump(exclude={'templates'}),
        'proxmox_template':profile.templates['proxmox'].vm_id,'deployer_template':profile.templates['deployer'].vm_id})


def require_workshop_profile(context, plan: dict):
    profile = installation_profile(context)
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
    if profile.managed_gateway and not plan.get('managed_gateway'):
        raise Range42Error(status=409, code='WORKSHOP_GATEWAY_REQUIRED',
            message='This environment requires one managed gateway per run. Review the run with its approved gateway resources.')
    if gateway := plan.get('managed_gateway'):
        if profile.managed_gateway is None:
            raise Range42Error(status=409, code='WORKSHOP_GATEWAY_PROFILE_REQUIRED',
                message='This environment has no approved managed gateway capability. Select an explicitly prepared profile before requesting one.')
        if (not any(start <= gateway['vm_id'] <= end for start, end in profile.vmid_ranges)
                or any(gateway.get(field) != getattr(profile.managed_gateway, field)
                       for field in ('management_bridge', 'management_subnet'))):
            raise Range42Error(status=409, code='WORKSHOP_PROVISIONING_SCOPE',
                message='Choose a gateway VM ID and management network within this environment’s approved profile.')
    return profile
