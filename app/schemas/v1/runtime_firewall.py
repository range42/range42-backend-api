"""Bounded policy gestures; target URLs and ownership comments are server-owned."""
from ipaddress import ip_network
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator

Name = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")]
Position = Annotated[StrictInt, Field(ge=0, le=4095)]


class FirewallScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["vm", "node", "datacenter"]
    vm_id: Annotated[StrictInt, Field(ge=100, le=999999999)] | None = None
    acknowledge_shared_scope: StrictBool
    review_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def valid_scope(self):
        if (self.scope == "vm") != (self.vm_id is not None):
            raise ValueError("Guest scope requires one VM ID; other scopes cannot include a guest")
        if not self.acknowledge_shared_scope:
            raise ValueError("Review and acknowledge the affected firewall scope")
        return self


class FirewallAlias(FirewallScope):
    kind: Literal["firewall_alias"]
    scope: Literal["vm", "datacenter"]
    action: Literal["create", "delete", "rename"]
    name: Name
    cidr: str | None = None
    new_name: Name | None = None

    @field_validator("cidr")
    @classmethod
    def canonical_network(cls, value):
        return str(ip_network(value)) if value is not None else None

    @model_validator(mode="after")
    def valid_action(self):
        if (self.action == "create") != (self.cidr is not None) or (self.action == "rename") != (self.new_name is not None):
            raise ValueError("Create requires a CIDR; rename requires a new name; deletion accepts neither")
        return self


class FirewallRuleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    direction: Literal["in", "out"]
    action: Literal["ACCEPT", "DROP", "REJECT"]
    protocol: Literal["tcp", "udp"]
    destination_port: str = Field(min_length=1, max_length=80, pattern=r"^[0-9:,]+$")
    source: str | None = Field(default=None, max_length=128)
    destination: str | None = Field(default=None, max_length=128)
    enabled: StrictBool = True

    @field_validator("destination_port")
    @classmethod
    def valid_ports(cls, value):
        if len(value.split(",")) > 16:
            raise ValueError("Too many port ranges")
        for part in value.split(","):
            limits = [int(token) for token in part.split(":")]
            if not 1 <= len(limits) <= 2 or any(not 1 <= port <= 65535 for port in limits) or limits[0] > limits[-1]:
                raise ValueError("Use ports 1–65535 or ascending port ranges")
        return value

    @field_validator("source", "destination")
    @classmethod
    def literal_address_or_alias(cls, value):
        if value is None:
            return None
        if re.fullmatch(r"(?:dc/)?[A-Za-z][A-Za-z0-9_-]{0,63}", value):
            return value
        return str(ip_network(value))


class FirewallRule(FirewallScope):
    kind: Literal["firewall_rule"]
    action: Literal["create", "update", "delete", "move"]
    position: Position | None = None
    move_to: Position | None = None
    name: Name | None = None
    rule: FirewallRuleBody | None = None

    @model_validator(mode="after")
    def valid_action(self):
        if (self.action != "create") != (self.position is not None):
            raise ValueError("Existing rules require a reviewed position; new rules are inserted first")
        if (self.action == "create") != (self.name is not None):
            raise ValueError("New rules require a stable name; existing rule names are preserved")
        if (self.action == "move") != (self.move_to is not None):
            raise ValueError("Only a move requires a destination position")
        if (self.action in ("create", "update")) != (self.rule is not None):
            raise ValueError("Create and update require a complete rule")
        return self
