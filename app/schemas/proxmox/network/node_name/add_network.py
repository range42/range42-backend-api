

from enum import Enum
from typing import List, Literal
from pydantic import BaseModel, Field

#
# ISSUE - #11
#

#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Request_ProxmoxNetwork_WithNodeName_AddNetworkInterface(BaseModel):

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

    #

    bridge_ports: str | None = Field(
        default=None,
        description="Bridge ports",
        pattern=r"^[a-zA-Z0-9._-]+$"
    )

    iface_name: str | None = Field(
        ...,
        description="Interface name",
        pattern=r"^[a-zA-Z0-9._-]+$"
    )

    iface_type: str | None = Field(
        ...,
        description="Interface type - ethernet, ovs, bridge",
        pattern=r"^[a-zA-Z]+$"
    )

    iface_autostart: int | None = Field(
        ...,
        description="Autostart flag - 0 = no, 1 = yes"
    )

    ip_address: str | None = Field(
        default=None,
        description="ipv4 address",
        pattern=r"^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$"
    )

    ip_netmask: str | None = Field(
        default=None,
        description="ipv4 netmask",
        pattern=r"^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$|^\/[0-9]{1,2}$"
    )

    ip_gateway: str | None = Field(
        default=None,
        description="ipv4 gateway",
        pattern=r"^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$"
    )

    ovs_bridge: str | None = Field(
        default=None,
        description="OVS bridge name",
        pattern=r"^[a-zA-Z0-9._-]+$"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "as_json": "true",
                #
                "iface_name": "vmbr142",
                "iface_type": "bridge",
                "bridge_ports": "enp87s0",
                "iface_autostart": 1,
                "ip_address": "192.168.99.2",
                "ip_netmask": "255.255.255.0"
            }
        }
    }

#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Reply_ProxmoxNetwork_WithNodeName_AddNetworkInterfaceItem(BaseModel):

    action: Literal["vm_DeleteIptablesRule_usage"]
    source: Literal["proxmox"]
    proxmox_node: str
    as_json: bool
    ##
    # vm_id: int = Field(..., ge=1)
    # vm_id: str
    # vm_fw_pos: int

    bridge_ports: str
    iface_name: str
    iface_type: str
    iface_autostart: int
    ip_address: str
    ip_netmask: str
    ip_gateway: str
    ovs_bridge: str



class Reply_ProxmoxNetwork_WithNodeName_AddNetworkInterface(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[Reply_ProxmoxNetwork_WithNodeName_AddNetworkInterfaceItem]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {

                        "action": "network_add_interfaces_node",
                        "proxmox_node": "px-testing",
                        "source": "proxmox",
                        #
                        "bridge_ports": "enp87s0",
                        "iface_autostart": "1",
                        "iface_name": "vmbr142",
                        "ip_address": "192.168.99.2",
                        "ip_netmask": "255.255.255.0",
                    }
                ]
            }
        }
    }
