"""Consolidated firewall schemas: rules, aliases, enable/disable at DC/node/VM level."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Add Iptables Alias
# ---------------------------------------------------------------------------


class FirewallAliasAddRequest(BaseModel):
    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description="Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$",
    )

    as_json: bool = Field(
        default=True, description="If true : JSON output else : raw output"
    )
    #

    vm_id: str = Field(
        ...,
        # default="4000",
        description="Virtual machine id",
        pattern=r"^[0-9]+$",
    )

    vm_fw_alias_name: str = Field(
        ...,
        description="Firewall alias name",
        pattern=r"^[A-Za-z0-9-_]+$",
    )

    vm_fw_alias_cidr: str = Field(
        ...,
        description="CIDR notation for the alias - eg 192.168.123.0/24",
        pattern=r"^[0-9./]+$",
    )

    vm_fw_alias_comment: str | None = Field(
        ...,
        description="Optional comment for the firewall alias",
        pattern=r"^[A-Za-z0-9 _-]*$",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "as_json": True,
                #
                "vm_id": "1000",
                #
                "vm_fw_alias_name": "test",
                "vm_fw_alias_cidr": "192.168.123.0/24",
                "vm_fw_alias_comment": "this_comment",
            }
        }
    }


class FirewallAliasAddItemReply(BaseModel):
    action: Literal["vm_ListIso_usage"]
    source: Literal["proxmox"]
    proxmox_node: str
    ##
    # vm_id: int = Field(..., ge=1)
    vm_fw_alias_cidr: str
    vm_fw_alias_name: str
    vm_id: str


class FirewallAliasAddReply(BaseModel):
    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[FirewallAliasAddItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "firewall_vm_add_iptables_alias",
                        "source": "proxmox",
                        "proxmox_node": "px-testing",
                        ##
                        "vm_fw_alias_cidr": "192.168.123.0/24",
                        "vm_fw_alias_name": "test",
                        "vm_id": "1000",
                    }
                ],
            }
        }
    }


# ---------------------------------------------------------------------------
# Apply Iptables Rules
# ---------------------------------------------------------------------------


class FirewallRuleApplyRequest(BaseModel):
    proxmox_node: str = Field(
        ...,
        description="Target Proxmox node name.",
        pattern=r"^[A-Za-z0-9-]+$",
    )

    as_json: bool = Field(
        default=True,
        description="If true: return JSON output, otherwise raw output.",
    )
    #

    vm_id: str = Field(
        ...,
        # default="4000",
        description="Virtual machine id",
        pattern=r"^[0-9]+$",
    )

    vm_fw_action: str = Field(
        ...,
        description="Firewall action -  ACCEPT, DROP, REJECT",
        pattern=r"^(ACCEPT|DROP|REJECT)$",
    )

    vm_fw_dport: str = Field(
        ...,
        description="Destination port or port range",
        pattern=r"^[0-9:-]+$",
    )

    vm_fw_enable: int = Field(
        ...,
        description="Enable flag - 1 = enabled, 0 = disabled",
    )

    vm_fw_proto: str = Field(
        ...,
        description="Protocol - tcp, udp, icmp",
        pattern=r"^[a-zA-Z0-9]+$",
    )

    vm_fw_type: str = Field(
        ...,
        description="Rule type - in or out",
        pattern=r"^(in|out)$",
    )

    vm_fw_log: str | None = Field(
        default=None,
        description="Optional logging level - info, debug,...",
        pattern=r"^[A-Za-z0-9_-]+$",
    )

    vm_fw_iface: str | None = Field(
        default=None,
        description="Optional network interface name",
        pattern=r"^[A-Za-z0-9_-]+$",
    )

    vm_fw_source: str | None = Field(
        default=None,
        description="Optional source address or CIDR",
        pattern=r"^[0-9./]+$",
    )

    vm_fw_dest: str | None = Field(
        default=None,
        description="Optional destination address or CIDR",
        pattern=r"^[0-9./]+$",
    )

    vm_fw_sport: str | None = Field(
        default=None,
        description="Optional source port or port range",
        pattern=r"^[0-9:-]+$",
    )

    vm_fw_comment: str | None = Field(
        default=None,
        description="Optional comment for the rule",
        pattern=r"^[A-Za-z0-9 _-]*$",
    )

    vm_fw_pos: int | None = Field(
        default=None,
        description="Optional position index rule in the chain.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": [
                {
                    "proxmox_node": "px-node-01",
                    "as_json": True,
                    #
                    "vm_id": "1000",
                    "vm_fw_action": "ACCEPT",
                    "vm_fw_type": "in",
                    "vm_fw_proto": "tcp",
                    "vm_fw_dport": "22",
                    "vm_fw_enable": 1,
                    "vm_fw_iface": "net0",
                    "vm_fw_source": "192.168.1.0/24",
                    "vm_fw_dest": "0.0.0.0/0",
                    "vm_fw_sport": "1024",
                    "vm_fw_comment": "Test comment",
                    "vm_fw_pos": 5,
                    "vm_fw_log": "debug",
                },
            ]
        }
    )


class FirewallRuleApplyItemReply(BaseModel):
    action: Literal["vm_ApplyIptablesRules_usage"]
    source: Literal["proxmox"]
    proxmox_node: str
    ##
    # vm_id: int = Field(..., ge=1)
    vm_fw_action: str
    vm_fw_comment: str
    vm_fw_dest: str
    vm_fw_dport: str
    vm_fw_enable: int
    vm_fw_iface: str
    vm_fw_log: str
    vm_fw_pos: int
    vm_fw_proto: str
    vm_fw_source: str
    vm_fw_sport: str
    vm_fw_type: str
    vm_id: str


class FirewallRuleApplyReply(BaseModel):
    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[FirewallRuleApplyItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "firewall_vm_apply_iptables_rule",
                        "proxmox_node": "px-testing",
                        "source": "proxmox",
                        #
                        "vm_fw_action": "ACCEPT",
                        "vm_fw_dport": "80",
                        "vm_fw_enable": "1",
                        "vm_fw_proto": "tcp",
                        "vm_fw_type": "out",
                        "vm_id": "100",
                    },
                ],
            }
        }
    }


# ---------------------------------------------------------------------------
# Delete Iptables Alias
# ---------------------------------------------------------------------------


class FirewallAliasDeleteRequest(BaseModel):
    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description="Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$",
    )

    as_json: bool = Field(
        default=True, description="If true : JSON output else : raw output"
    )
    #

    vm_id: str = Field(
        ...,
        # default="4000",
        description="Virtual machine id",
        pattern=r"^[0-9]+$",
    )

    vm_fw_alias_name: str = Field(
        ...,
        description="Firewall alias name",
        pattern=r"^[A-Za-z0-9-_]+$",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "as_json": True,
                #
                "vm_id": "1000",
                "vm_fw_alias_name": "test",
            }
        }
    }


class FirewallAliasDeleteItemReply(BaseModel):
    action: Literal["firewall_vm_delete_iptables_alias"]
    source: Literal["proxmox"]
    proxmox_node: str
    # ##
    # vm_id: int = Field(..., ge=1)
    vm_fw_alias_name: str
    vm_id: int


class FirewallAliasDeleteReply(BaseModel):
    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[FirewallAliasDeleteItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "firewall_vm_delete_iptables_alias",
                        "source": "proxmox",
                        "proxmox_node": "px-testing",
                        ##
                        "vm_fw_alias_name": "test",
                        "vm_id": "1000",
                    }
                ],
            }
        }
    }


# ---------------------------------------------------------------------------
# Delete Iptables Rule
# ---------------------------------------------------------------------------


class FirewallRuleDeleteRequest(BaseModel):
    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description="Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$",
    )

    as_json: bool = Field(
        default=True, description="If true : JSON output else : raw output"
    )
    #

    vm_id: str = Field(
        ...,
        # default="4000",
        description="Virtual machine id",
        pattern=r"^[0-9]+$",
    )

    vm_fw_pos: int | None = Field(
        ...,
        description="Optional position index rule in the chain.",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "as_json": True,
                #
                "vm_id": "1000",
                "vm_fw_pos": 1,
            }
        }
    }


class FirewallRuleDeleteItemReply(BaseModel):
    action: Literal["vm_DeleteIptablesRule_usage"]
    source: Literal["proxmox"]
    proxmox_node: str
    ##
    # vm_id: int = Field(..., ge=1)
    vm_id: str
    vm_fw_pos: int


class FirewallRuleDeleteReply(BaseModel):
    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[FirewallRuleDeleteItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "firewall_vm_delete_iptables_rule",
                        "source": "proxmox",
                        "proxmox_node": "px-testing",
                        ##
                        "vm_fw_pos": "0",
                        "vm_id": "1000",
                    }
                ],
            }
        }
    }


# ---------------------------------------------------------------------------
# List Iptables Alias
# ---------------------------------------------------------------------------


class FirewallAliasListRequest(BaseModel):
    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description="Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$",
    )

    as_json: bool = Field(
        default=True, description="If true : JSON output else : raw output"
    )
    #

    vm_id: str = Field(
        ...,
        # default="4000",
        description="Virtual machine id",
        pattern=r"^[0-9]+$",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "as_json": True,
                #
                "vm_id": "1000",
            }
        }
    }


class FirewallAliasListItemReply(BaseModel):
    action: Literal["vm_ListIptablesAlias_usage"]
    source: Literal["proxmox"]
    proxmox_node: str
    ##
    # vm_id: int = Field(..., ge=1)
    vm_fw_alias_cidr: str
    vm_fw_alias_name: int
    vm_id: str


class FirewallAliasListReply(BaseModel):
    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[FirewallAliasListItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "firewall_vm_list_iptables_alias",
                        "source": "proxmox",
                        "proxmox_node": "px-testing",
                        ##
                        "vm_fw_alias_cidr": "192.168.123.0/24",
                        "vm_fw_alias_name": "test",
                        "vm_id": "1000",
                    }
                ],
            }
        }
    }


# ---------------------------------------------------------------------------
# List Iptables Rules
# ---------------------------------------------------------------------------


class FirewallRuleListRequest(BaseModel):
    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description="Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$",
    )

    as_json: bool = Field(
        default=True, description="If true : JSON output else : raw output"
    )
    #

    vm_id: str = Field(
        ...,
        # default="4000",
        description="Virtual machine id",
        pattern=r"^[0-9]+$",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "as_json": True,
                #
                "vm_id": "1000",
            }
        }
    }


class FirewallRuleListItemReply(BaseModel):
    action: Literal["vm_ListIptablesRules_usage"]
    source: Literal["proxmox"]
    proxmox_node: str
    ##
    # vm_id: int = Field(..., ge=1)
    vm_fw_action: str
    vm_fw_comment: str
    vm_fw_dest: str
    vm_fw_dport: str
    vm_fw_enable: int
    vm_fw_iface: str
    vm_fw_log: str
    vm_fw_pos: int
    vm_fw_proto: str
    vm_fw_source: str
    vm_fw_sport: str
    vm_fw_type: str
    vm_id: str


class FirewallRuleListReply(BaseModel):
    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[FirewallRuleListItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "firewall_vm_list_iptables_rule",
                        "proxmox_node": "px-testing",
                        "source": "proxmox",
                        "vm_fw_action": "ACCEPT",
                        "vm_fw_enable": 0,
                        "vm_fw_log": "nolog",
                        "vm_fw_pos": 0,
                        "vm_fw_type": "in",
                        "vm_id": "100",
                    },
                ],
            }
        }
    }


# ---------------------------------------------------------------------------
# Enable / Disable Firewall — Datacenter
# ---------------------------------------------------------------------------


class FirewallEnableDcRequest(BaseModel):
    proxmox_api_host: str = Field(
        ...,
        # default= "px-testing",
        description="Proxmox api - ip:port",
        pattern=r"^[A-Za-z0-9\.:-]*$",
    )

    as_json: bool = Field(
        default=True, description="If true : JSON output else : raw output"
    )
    #

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "as_json": True,
                #
                "proxmox_api_host": "127.0.0.1:18007",
            }
        }
    }


class FirewallEnableDcItemReply(BaseModel):
    action: Literal["vm_EnableFirewallDc_usage"]
    source: Literal["proxmox"]
    proxmox_node: str
    ##
    # vm_id: int = Field(..., ge=1)
    proxmox_api_host: str


class FirewallEnableDcReply(BaseModel):
    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[FirewallEnableDcItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "firewall_vm_disable",
                        "source": "proxmox",
                        "proxmox_node": "px-testing",
                        ##
                        "vm_id": "100",
                        "vm_firewall": "disable",
                        "vm_name": "test",
                    }
                ],
            }
        }
    }


class FirewallDisableDcRequest(BaseModel):
    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description="Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$",
    )

    as_json: bool = Field(
        default=True, description="If true : JSON output else : raw output"
    )
    #

    proxmox_api_host: str = Field(
        ...,
        # default= "px-testing",
        description="Proxmox api - ip:port",
        pattern=r"^[A-Za-z0-9\.:-]*$",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "as_json": True,
                #
                "proxmox_api_host": "127.0.0.1:1234",
            }
        }
    }


class FirewallDisableDcItemReply(BaseModel):
    action: Literal["vm_DisableFirewallDc_usage"]
    source: Literal["proxmox"]
    proxmox_node: str
    ##


class FirewallDisableDcReply(BaseModel):
    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[FirewallDisableDcItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "storage_list_iso",
                        "source": "proxmox",
                        "proxmox_node": "px-testing",
                        ##
                    }
                ],
            }
        }
    }


# ---------------------------------------------------------------------------
# Enable / Disable Firewall — Node
# ---------------------------------------------------------------------------


class FirewallEnableNodeRequest(BaseModel):
    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description="Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$",
    )

    as_json: bool = Field(
        default=True, description="If true : JSON output else : raw output"
    )
    #

    model_config = {
        "json_schema_extra": {
            "example": {"proxmox_node": "px-testing", "as_json": True}
        }
    }


class FirewallEnableNodeItemReply(BaseModel):
    action: Literal["vm_EnableFirewallNode_usage"]
    source: Literal["proxmox"]
    proxmox_node: str
    ##
    # vm_id: int = Field(..., ge=1)
    node_firewall: str


class FirewallEnableNodeReply(BaseModel):
    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[FirewallEnableNodeItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "firewall_node_enable",
                        "source": "proxmox",
                        "proxmox_node": "px-testing",
                        ##
                        "node_firewall": "enabled",
                    }
                ],
            }
        }
    }


class FirewallDisableNodeRequest(BaseModel):
    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description="Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$",
    )

    as_json: bool = Field(
        default=True, description="If true : JSON output else : raw output"
    )
    #

    model_config = {
        "json_schema_extra": {
            "example": {"proxmox_node": "px-testing", "as_json": True}
        }
    }


class FirewallDisableNodeItemReply(BaseModel):
    action: Literal["vm_EnableFirewallNode_usage"]
    source: Literal["proxmox"]
    proxmox_node: str
    ##
    # vm_id: int = Field(..., ge=1)
    node_firewall: str


class FirewallDisableNodeReply(BaseModel):
    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[FirewallDisableNodeItemReply]

    #
    # missing feat in role ?
    #
    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "firewall_node_enable",
                        "source": "proxmox",
                        "proxmox_node": "px-testing",
                        ##
                        "node_firewall": "disabled",
                    }
                ],
            }
        }
    }


# ---------------------------------------------------------------------------
# Enable / Disable Firewall — VM
# ---------------------------------------------------------------------------


class FirewallEnableVmRequest(BaseModel):
    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description="Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$",
    )

    as_json: bool = Field(
        default=True, description="If true : JSON output else : raw output"
    )
    #

    vm_id: str = Field(
        ...,
        # default="4000",
        description="Virtual machine id",
        pattern=r"^[0-9]+$",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "as_json": True,
                #
                "vm_name": "test",
                "vm_id": "1000",
            }
        }
    }


class FirewallEnableVmItemReply(BaseModel):
    action: Literal["vm_EnableFirewallVm_usage"]
    source: Literal["proxmox"]
    proxmox_node: str
    ##
    # vm_id: int = Field(..., ge=1)
    vm_id: str
    vm_name: str
    vm_firewall: str


class FirewallEnableVmReply(BaseModel):
    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[FirewallEnableVmItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "firewall_vm_enable",
                        "source": "proxmox",
                        "proxmox_node": "px-testing",
                        ##
                        "vm_id": "100",
                        "vm_firewall": "enabled",
                        "vm_name": "test",
                    }
                ],
            }
        }
    }


class FirewallDisableVmRequest(BaseModel):
    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description="Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$",
    )

    as_json: bool = Field(
        default=True, description="If true : JSON output else : raw output"
    )
    #

    vm_id: str = Field(
        ...,
        # default="4000",
        description="Virtual machine id",
        pattern=r"^[0-9]+$",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "as_json": True,
                #
                "vm_id": "1000",
            }
        }
    }


class FirewallDisableVmItemReply(BaseModel):
    action: Literal["vm_EnableFirewallDc_usage"]
    source: Literal["proxmox"]
    proxmox_node: str
    ##
    # vm_id: int = Field(..., ge=1)
    proxmox_api_host: str


class FirewallDisableVmReply(BaseModel):
    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[FirewallDisableVmItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "firewall_vm_disable",
                        "source": "proxmox",
                        "proxmox_node": "px-testing",
                        ##
                        "vm_id": "1000",
                        "vm_firewall": "disable",
                        "vm_name": "test",
                    }
                ],
            }
        }
    }


# ---------------------------------------------------------------------------
# Backward compatibility -- old names used by current routes
# ---------------------------------------------------------------------------

# firewall/add_iptable_alias.py
Request_ProxmoxFirewall_AddIptablesAlias = FirewallAliasAddRequest
Reply_ProxmoxFirewallWithStorageName_AddIptablesAliasItem = FirewallAliasAddItemReply
Reply_ProxmoxFirewallWithStorageName_AddIptablesAlias = FirewallAliasAddReply

# firewall/apply_iptables_rules.py
Request_ProxmoxFirewall_ApplyIptablesRules = FirewallRuleApplyRequest
Reply_ProxmoxFirewallWithStorageName_ApplyIptablesRulesItem = FirewallRuleApplyItemReply
Reply_ProxmoxFirewallWithStorageName_ApplyIptablesRules = FirewallRuleApplyReply

# firewall/delete_iptables_alias.py
Request_ProxmoxFirewall_DeleteIptablesAlias = FirewallAliasDeleteRequest
Reply_ProxmoxFirewallWithStorageName_DeleteIptablesAliasItem = (
    FirewallAliasDeleteItemReply
)
Reply_ProxmoxFirewallWithStorageName_DeleteIptablesAlias = FirewallAliasDeleteReply

# firewall/delete_iptables_rule.py
Request_ProxmoxFirewall_DeleteIptablesRule = FirewallRuleDeleteRequest
Reply_ProxmoxFirewallWithStorageName_DeleteIptablesRuleItem = (
    FirewallRuleDeleteItemReply
)
Reply_ProxmoxFirewallWithStorageName_DeleteIptablesRule = FirewallRuleDeleteReply

# firewall/list_iptables_alias.py
Request_ProxmoxFirewall_ListIptablesAlias = FirewallAliasListRequest
Reply_ProxmoxFirewallWithStorageName_ListIptablesAliasItem = FirewallAliasListItemReply
Reply_ProxmoxFirewallWithStorageName_ListIptablesAlias = FirewallAliasListReply

# firewall/list_iptables_rules.py
Request_ProxmoxFirewall_ListIptablesRules = FirewallRuleListRequest
Reply_ProxmoxFirewallWithStorageName_ListIptablesRulesItem = FirewallRuleListItemReply
Reply_ProxmoxFirewallWithStorageName_ListIptablesRules = FirewallRuleListReply

# firewall/enable_firewall_dc.py
Request_ProxmoxFirewall_EnableFirewallDc = FirewallEnableDcRequest
Reply_ProxmoxFirewallWithStorageName_EnableFirewallDcItem = FirewallEnableDcItemReply
Reply_ProxmoxFirewallWithStorageName_EnableFirewallDc = FirewallEnableDcReply

# firewall/disable_firewall_dc.py
Request_ProxmoxFirewall_DisableFirewallDc = FirewallDisableDcRequest
Reply_ProxmoxFirewallWithStorageName_DisableFirewallDcItem = FirewallDisableDcItemReply
Reply_ProxmoxFirewallWithStorageName_DisableFirewallDc = FirewallDisableDcReply

# firewall/enable_firewall_node.py
Request_ProxmoxFirewall_EnableFirewallNode = FirewallEnableNodeRequest
Reply_ProxmoxFirewallWithStorageName_EnableFirewallNodeItem = (
    FirewallEnableNodeItemReply
)
Reply_ProxmoxFirewallWithStorageName_EnableFirewallNode = FirewallEnableNodeReply

# firewall/disable_firewall_node.py
Request_ProxmoxFirewall_DistableFirewallNode = FirewallDisableNodeRequest
Reply_ProxmoxFirewallWithStorageName_DistableFirewallNodeItem = (
    FirewallDisableNodeItemReply
)
Reply_ProxmoxFirewallWithStorageName_DisableFirewallNode = FirewallDisableNodeReply

# firewall/enable_firewall_vm.py
Request_ProxmoxFirewall_EnableFirewallVm = FirewallEnableVmRequest
Reply_ProxmoxFirewallWithStorageName_EnableFirewallVmItem = FirewallEnableVmItemReply
Reply_ProxmoxFirewallWithStorageName_EnableFirewallVm = FirewallEnableVmReply

# firewall/disable_firewall_vm.py
Request_ProxmoxFirewall_DistableFirewallVm = FirewallDisableVmRequest
Reply_ProxmoxFirewallWithStorageName_DistableFirewallVmItem = FirewallDisableVmItemReply
Reply_ProxmoxFirewallWithStorageName_DisableFirewallVm = FirewallDisableVmReply
