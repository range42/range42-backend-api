"""Additive VM manifest validation; the first NIC is the management interface."""
from __future__ import annotations

from ipaddress import IPv4Address
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator, model_validator

VmId = Annotated[StrictInt, Field(ge=100, le=999999999)]
Bridge = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,14}$")]


class Nic(BaseModel):
    model_config = ConfigDict(extra="allow")
    index: Annotated[StrictInt, Field(ge=0, le=31)]
    bridge: Bridge
    ip: IPv4Address
    prefix: Annotated[StrictInt, Field(ge=1, le=30)] | None = None
    gateway: IPv4Address | None = None


class ResourceOverrides(BaseModel):
    model_config = ConfigDict(extra="allow")
    storage: str | None = Field(default=None, strict=True, pattern=r"^[A-Za-z][A-Za-z0-9._-]{0,63}$")
    cores: Annotated[StrictInt, Field(ge=1, le=128)] | None = None
    memory_mb: Annotated[StrictInt, Field(ge=128, le=1048576)] | None = None
    disk_gb: Annotated[StrictInt, Field(ge=1, le=65536)] | None = None
    disk_device: str | None = Field(default=None, pattern=r"^(?:scsi(?:[0-9]|[12][0-9]|30)|virtio(?:[0-9]|1[0-5])|sata[0-5])$")


class CloudInit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ssh_user: str = Field(strict=True, pattern=r"^[a-z_][a-z0-9_-]{0,31}$")
    dns_servers: list[StrictStr] | None = Field(min_length=1, max_length=3)
    dns_search_domain: str | None = Field(strict=True, max_length=253,
        pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")

    @field_validator("dns_servers")
    @classmethod
    def dns_addresses(cls, value):
        for address in value or []:
            IPv4Address(address)
        return value


class Vm(ResourceOverrides):
    vm_id: VmId
    vm_name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9-]{0,62}$")
    template_vm_id: VmId | None = None
    ip: IPv4Address
    bridge: Bridge
    nics: list[Nic] = Field(min_length=1, max_length=32)
    cloud_init: CloudInit | None = None

    @model_validator(mode="after")
    def management_nic(self):
        if [nic.index for nic in self.nics] != list(range(len(self.nics))):
            raise ValueError("NIC indexes must be contiguous and start at zero")
        if (self.ip, self.bridge) != (self.nics[0].ip, self.nics[0].bridge):
            raise ValueError("Top-level ip and bridge must match the management NIC")
        if self.disk_gb is not None and self.disk_device is None:
            raise ValueError("Disk growth requires an explicit disk device")
        return self


def validate_vm_manifest(document: dict) -> dict:
    """Validate v3 without rewriting it; older concrete manifests remain valid."""
    if not isinstance(document, dict):
        raise ValueError("VM manifest must be an object")
    preferences = document.get("guest_preferences_version")
    rows = document.get("vms")
    explicit_storage = isinstance(rows, list) and any(isinstance(vm, dict) and "storage" in vm for vm in rows)
    explicit_cloud_init = isinstance(rows, list) and any(isinstance(vm, dict) and "cloud_init" in vm for vm in rows)
    if preferences is not None or explicit_storage or explicit_cloud_init:
        if type(preferences) is not int or preferences not in (1, 2) or document.get("version") != 3:
            raise ValueError("Explicit guest preferences require version 1 or 2 and VM manifest version 3")
        if not isinstance(document.get("vms"), list) or any(not isinstance(vm, dict) or "storage" not in vm for vm in document["vms"]):
            raise ValueError("Every VM must declare selected or inherited storage")
        if explicit_cloud_init and preferences != 2:
            raise ValueError("Cloud-init requires guest preferences version 2")
        if preferences == 2 and any(not isinstance(vm.get("cloud_init"), dict) for vm in rows):
            raise ValueError("Every VM must declare cloud-init preferences")
    if document.get("version", 2) in (1, 2):
        for vm in document.get("vms", []):
            ResourceOverrides.model_validate(vm)
        return document
    if document.get("version") != 3 or not isinstance(document.get("vms"), list):
        raise ValueError("Unsupported VM manifest version or VM list")
    vms = [Vm.model_validate(vm) for vm in document["vms"]]
    ids = {vm.vm_id for vm in vms}
    if len(ids) != len(vms) or len({vm.vm_name for vm in vms}) != len(vms):
        raise ValueError("VM IDs and names must be unique")
    if any(vm.template_vm_id in ids for vm in vms):
        raise ValueError("A template cannot also be a deployment target")
    addresses = [(nic.bridge, nic.ip) for vm in vms for nic in vm.nics]
    if len(set(addresses)) != len(addresses):
        raise ValueError("Duplicate NIC address on the same network")
    return document
