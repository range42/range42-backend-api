

from enum import Enum
from typing import List, Literal
from pydantic import BaseModel, Field, ConfigDict

#
# ISSUE - #12
#

#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Request_ProxmoxFirewall_ApplyIptablesRules(BaseModel):

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
        pattern=r"^[0-9]+$"
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
            "example": {
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
            }
        }
    )

#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Reply_ProxmoxFirewallWithStorageName_ApplyIptablesRulesItem(BaseModel):

    action: Literal["vm_ApplyIptablesRules_usage"]
    source: Literal["proxmox"]
    proxmox_node: str
    ##
    # vm_id: int = Field(..., ge=1)
    vm_fw_action   : str
    vm_fw_comment  : str
    vm_fw_dest     : str
    vm_fw_dport    : str
    vm_fw_enable   : int
    vm_fw_iface    : str
    vm_fw_log      : str
    vm_fw_pos      : int
    vm_fw_proto    : str
    vm_fw_source   : str
    vm_fw_sport    : str
    vm_fw_type     : str
    vm_id          : str

class Reply_ProxmoxFirewallWithStorageName_ApplyIptablesRules(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[Reply_ProxmoxFirewallWithStorageName_ApplyIptablesRulesItem]

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
                        "vm_id": "100"
                    },
                    {
                        "action": "firewall_vm_apply_iptables_rule",
                        "proxmox_node": "px-testing",
                        "source": "proxmox",
                        #
                        "vm_fw_action": "ACCEPT",
                        "vm_fw_comment": "Test comment",
                        "vm_fw_dest": "0.0.0.0/0",
                        "vm_fw_dport": "22",
                        "vm_fw_enable": "1",
                        "vm_fw_iface": "net0",
                        "vm_fw_log": "warning",
                        "vm_fw_pos": "5",
                        "vm_fw_proto": "tcp",
                        "vm_fw_source": "192.168.1.0/24",
                        "vm_fw_sport": "1024",
                        "vm_fw_type": "in",
                        "vm_id": "100"

                    }
                ]
            }
        }
    }
