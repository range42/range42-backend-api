"""Installed runtime support for concrete scenario authoring."""
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core import runtime_operations
from app.core.errors import Range42Error
from app.core.scenario_resources import bootstrap_features
from app.core.scenario_firewall import management_access_authorized
from app.core.native_sdn import NATIVE_CONTRACT

router = APIRouter()


class RuntimeCapabilities(BaseModel):
    version: Literal[1] = 1
    available: bool
    contract: str | None = None
    fingerprint: str | None = None
    operations: list[Literal["vm_firewall", "scenario_firewall", "sdn_snat", "host_firewall", "runtime_observe", "sdn_network", "firewall_alias", "firewall_rule"]] = Field(default_factory=list)
    bootstrap_features: list[Literal["extra_nics", "resources", "disk_resize"]] = Field(default_factory=list)
    reason: str | None = None
    management_access_available: bool = False


@router.get("/runtime-capabilities", response_model=RuntimeCapabilities)
def runtime_capabilities():
    try:
        profile = runtime_operations.operation_profile("vm_firewall")
    except Range42Error:
        return RuntimeCapabilities(available=False, reason="Runtime support is unverified. Install a reviewed runtime and its dependency manifest.")
    return RuntimeCapabilities(available=True, contract=profile.get("contract"), fingerprint=profile["fingerprint"],
                               operations=profile["operations"], bootstrap_features=sorted(bootstrap_features()),
                               management_access_available=profile.get("contract") == NATIVE_CONTRACT and management_access_authorized())
