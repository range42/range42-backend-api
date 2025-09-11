

from enum import Enum
from typing import List, Literal
from pydantic import BaseModel, Field

#
# ISSUE - #12
#

#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Request_ProxmoxFirewall_ListIptablesRules(BaseModel):

    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description = "Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$"
    )

    vm_id: str = Field(
        ...,
        # default="4000",
        description="Virtual machine id",
        pattern=r"^[0-9]+$"
    )

    as_json: bool = Field(
        default=True,
        description="If true : JSON output else : raw output"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "storage_name": "local",
                "as_json": True

            }
        }
    }

#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Reply_ProxmoxFirewallWithStorageName_ListIptablesRulesItem(BaseModel):

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

class Reply_ProxmoxFirewallWithStorageName_ListIptablesRules(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[Reply_ProxmoxFirewallWithStorageName_ListIptablesRulesItem]

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
                        "vm_id": "100"
                    },
                    {
                        "action": "firewall_vm_list_iptables_rule",
                        "proxmox_node": "px-testing",
                        "source": "proxmox",
                        "vm_fw_action": "ACCEPT",
                        "vm_fw_comment": "Test comment",
                        "vm_fw_dest": "0.0.0.0/0",
                        "vm_fw_dport": "22",
                        "vm_fw_enable": 1,
                        "vm_fw_iface": "net0",
                        "vm_fw_log": "warning",
                        "vm_fw_pos": 1,
                        "vm_fw_proto": "tcp",
                        "vm_fw_source": "192.168.1.0/24",
                        "vm_fw_sport": "1024",
                        "vm_fw_type": "in",
                        "vm_id": "100"
                    },
                    {
                        "action": "firewall_vm_list_iptables_rule",
                        "proxmox_node": "px-testing",
                        "source": "proxmox",
                        "vm_fw_action": "ACCEPT",
                        "vm_fw_dport": "80",
                        "vm_fw_enable": 1,
                        "vm_fw_pos": 2,
                        "vm_fw_proto": "tcp",
                        "vm_fw_type": "out",
                        "vm_id": "100"
                    }

                ]
            }
        }
    }
