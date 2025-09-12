

from enum import Enum
from typing import List, Literal
from pydantic import BaseModel, Field

#
# ISSUE - #11
#

#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Request_ProxmoxNetwork_WithNodeName_ListInterface(BaseModel):

    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description = "Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$"
    )

    as_json: bool = Field(
        default=True,
        description="If true : JSON output else : raw output"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "as_json": True,
            }
        }
    }

#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Reply_ProxmoxNetwork_WithNodeName_ListInterfaceItem(BaseModel):

    action: Literal["vm_DeleteIptablesRule_usage"]
    source: Literal["proxmox"]
    proxmox_node: str
    ##
    # vm_id: int = Field(..., ge=1)
    vm_id: str
    vm_fw_pos: int

class Reply_ProxmoxNetwork_WithNodeName_ListInterface(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[Reply_ProxmoxNetwork_WithNodeName_ListInterfaceItem]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "network_list_interfaces_node",
                        "iface": "wlp89s0",
                        "iface_priority": 8,
                        "ip_settings_method": "manual",
                        "ip_settings_method6": "manual",
                        "proxmox_node": "px-testing",
                        "source": "proxmox"
                    },
                    {
                        "action": "network_list_interfaces_node",
                        "iface": "enp2s0f0np0",
                        "iface_exists": 1,
                        "iface_priority": 5,
                        "ip_settings_method": "manual",
                        "ip_settings_method6": "manual",
                        "proxmox_node": "px-testing",
                        "source": "proxmox"
                    },
                    {
                        "action": "network_list_interfaces_node",
                        "iface": "vmbr0",
                        "iface_active": 1,
                        "iface_autostart": 1,
                        "iface_bridge_fd": "0",
                        "iface_bridge_ports": "enp88s0",
                        "iface_bridge_stp": "off",
                        "iface_priority": 7,
                        "ip_settings_address": "192.168.42.201",
                        "ip_settings_cidr": "192.168.42.201/24",
                        "ip_settings_gateway": "192.168.42.1",
                        "ip_settings_method": "static",
                        "ip_settings_method6": "manual",
                        "ip_settings_netmask": "24",
                        "proxmox_node": "px-testing",
                        "source": "proxmox"
                    },
                    {
                        "action": "network_list_interfaces_node",
                        "iface": "enp87s0",
                        "iface_active": 1,
                        "iface_exists": 1,
                        "iface_priority": 4,
                        "ip_settings_method": "manual",
                        "ip_settings_method6": "manual",
                        "proxmox_node": "px-testing",
                        "source": "proxmox"
                    },
                    {
                        "action": "network_list_interfaces_node",
                        "iface": "enp2s0f1np1",
                        "iface_exists": 1,
                        "iface_priority": 6,
                        "ip_settings_method": "manual",
                        "ip_settings_method6": "manual",
                        "proxmox_node": "px-testing",
                        "source": "proxmox"
                    },
                    {
                        "action": "network_list_interfaces_node",
                        "iface": "vmbr142",
                        "iface_autostart": 1,
                        "iface_bridge_fd": "0",
                        "iface_bridge_ports": "enp87s0",
                        "iface_bridge_stp": "off",
                        "iface_priority": 9,
                        "ip_settings_address": "192.168.99.2",
                        "ip_settings_cidr": "192.168.99.2/24",
                        "ip_settings_method": "static",
                        "ip_settings_method6": "manual",
                        "ip_settings_netmask": "24",
                        "proxmox_node": "px-testing",
                        "source": "proxmox"
                    },
                    {
                        "action": "network_list_interfaces_node",
                        "iface": "enp88s0",
                        "iface_active": 1,
                        "iface_exists": 1,
                        "iface_priority": 3,
                        "ip_settings_method": "manual",
                        "ip_settings_method6": "manual",
                        "proxmox_node": "px-testing",
                        "source": "proxmox"
                    }

                ]
            }
        }
    }
