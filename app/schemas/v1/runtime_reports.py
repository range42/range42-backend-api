"""Filtered configuration reports. Live observations carry separate provenance."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class FirewallRuleReport(BaseModel):
    position: int
    direction: str
    action: str
    enabled: bool
    source: str | None = None
    destination: str | None = None
    protocol: str | None = None
    destination_port: str | None = None
    source_port: str | None = None
    interface: str | None = None
    macro: str | None = None
    comment: str | None = None
    log: str | None = None


class FirewallAliasReport(BaseModel):
    name: str
    cidr: str
    comment: str | None = None


class FirewallChainReport(BaseModel):
    scope: Literal["datacenter", "node", "vm"]
    vm_id: int | None = None
    available: bool
    rules: list[FirewallRuleReport] = Field(default_factory=list)
    aliases: list[FirewallAliasReport] = Field(default_factory=list)
    error: str | None = None


class FirewallCardReport(BaseModel):
    vm_id: int
    index: int
    bridge: str | None
    filtering_configured: bool | None
    reasons: list[str]


class NativeNatRule(BaseModel):
    snat_source: str
    snat_out_iface: str
    snat_target: str
    snat_count: int
    snat_host: str
    proxmox_node: str


class LiveNatReport(BaseModel):
    available: bool = False
    observed_at: datetime | None = None
    attempt_id: str | None = None
    rules: list[NativeNatRule] = Field(default_factory=list)
    reason: str | None = "Run a native observation to read live NAT rules."


class FirewallSwitchesReport(BaseModel):
    datacenter_enabled: bool | None
    node_enabled: bool | None
    errors: list[str]


class SdnStateReport(BaseModel):
    pending_changes: bool | None
    errors: list[str]


class SdnNetworkReport(BaseModel):
    vnet: str
    zone: str
    subnet: str
    gateway: str | None
    manifest_snat: bool
    configured_snat: bool | None
    subnet_id: str | None
    identity_matches: bool | None
    active: bool | None
    live_forwarding_verified: Literal[False] = False


class RuntimeReport(BaseModel):
    version: Literal[1] = 1
    deployment_id: str
    target_host_id: str
    node_name: str
    project_sha: str | None = None
    observed_at: datetime
    partial: bool
    traffic_verified: Literal[False] = False
    switches: FirewallSwitchesReport
    sdn: SdnStateReport
    networks: list[SdnNetworkReport]
    chains: list[FirewallChainReport]
    cards: list[FirewallCardReport]
    live_nat: LiveNatReport = Field(default_factory=LiveNatReport)
