"""Explicit, bounded runtime desired-state operations."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator
from app.schemas.v1.runtime_firewall import FirewallAlias, FirewallRule


class DesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


class VmFirewall(DesiredState):
    kind: Literal["vm_firewall"]
    vm_id: Annotated[StrictInt, Field(ge=100, le=999999999)]


class ScenarioFirewall(DesiredState):
    kind: Literal["scenario_firewall"]


class SdnSnat(DesiredState):
    kind: Literal["sdn_snat"]
    vnet: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9]{0,7}$")
    acknowledge_shared_scope: StrictBool

    @model_validator(mode="after")
    def shared_scope(self):
        if not self.acknowledge_shared_scope:
            raise ValueError("Acknowledge that SDN apply and SNAT reconciliation affect the shared host")
        return self


class HostFirewall(DesiredState):
    kind: Literal["host_firewall"]
    acknowledge_shared_scope: StrictBool
    review_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def shared_scope(self):
        if not self.acknowledge_shared_scope:
            raise ValueError("Acknowledge the datacenter-wide switch and management access changes")
        return self


class RuntimeObserve(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["runtime_observe"]


class SdnNetwork(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["sdn_network"]
    action: Literal["create", "delete"]
    vnet: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9]{0,7}$")
    acknowledge_shared_scope: StrictBool
    review_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def shared_scope(self):
        if not self.acknowledge_shared_scope:
            raise ValueError("Acknowledge the cluster-wide SDN apply and selected VNet lifecycle change")
        return self


RuntimeOperation = Annotated[VmFirewall | ScenarioFirewall | SdnSnat | HostFirewall | RuntimeObserve | SdnNetwork | FirewallAlias | FirewallRule, Field(discriminator="kind")]
