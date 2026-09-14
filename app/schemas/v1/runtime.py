"""Explicit, bounded runtime desired-state operations."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator


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


RuntimeOperation = Annotated[VmFirewall | ScenarioFirewall | SdnSnat, Field(discriminator="kind")]
