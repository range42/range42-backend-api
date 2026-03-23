"""Consolidated network schemas: node and VM network interface operations."""

from typing import Literal
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Node — Add Network Interface
# ---------------------------------------------------------------------------

class NodeNetworkAddRequest(BaseModel):

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
            "example":[ {
                "proxmox_node": "px-testing",
                "as_json": "true",
                #
                "iface_name": "vmbr142",
                "iface_type": "bridge",
                "bridge_ports": "enp87s0",
                "iface_autostart": 1,
                "ip_address": "192.168.99.2",
                "ip_netmask": "255.255.255.0"
            },
            ]
        }
    }


class NodeNetworkAddItemReply(BaseModel):

    action: Literal["vm_DeleteIptablesRule_usage"]
    source: Literal["proxmox"]
    proxmox_node: str
    as_json: bool
    ##
    bridge_ports: str
    iface_name: str
    iface_type: str
    iface_autostart: int
    ip_address: str
    ip_netmask: str
    ip_gateway: str
    ovs_bridge: str


class NodeNetworkAddReply(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[NodeNetworkAddItemReply]

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


# ---------------------------------------------------------------------------
# Node — Delete Network Interface
# ---------------------------------------------------------------------------

class NodeNetworkDeleteRequest(BaseModel):

    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description = "Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$"
    )
    #

    iface_name: str | None = Field(
        description="Interface name",
        pattern=r"^[a-zA-Z0-9._-]+$"
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
                "as_json": True,
                #
                "iface_name":"vmbr42",
            }
        }
    }


class NodeNetworkDeleteItemReply(BaseModel):

    action: Literal["vm_DeleteIptablesRule_usage"]
    source: Literal["proxmox"]
    proxmox_node: str
    ##
    iface_name: str

class NodeNetworkDeleteReply(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[NodeNetworkDeleteItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "network_delete_interfaces_node",
                        "source": "proxmox",
                        "proxmox_node": "px-testing",
                        ##
                        "iface_name": "vmbr42",
                    }
                ]
            }
        }
    }


# ---------------------------------------------------------------------------
# Node — List Network Interfaces
# ---------------------------------------------------------------------------

class NodeNetworkListRequest(BaseModel):

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

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "as_json": True,
            }
        }
    }


class NodeNetworkListItemReply(BaseModel):

    action: Literal["vm_DeleteIptablesRule_usage"]
    source: Literal["proxmox"]
    proxmox_node: str
    ##
    # vm_id: int = Field(..., ge=1)
    vm_id: str
    vm_fw_pos: int

class NodeNetworkListReply(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[NodeNetworkListItemReply]

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
                ]
            }
        }
    }


# ---------------------------------------------------------------------------
# VM — Add Network Interface
# ---------------------------------------------------------------------------

class VmNetworkAddRequest(BaseModel):

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

    vm_id: str = Field(
        ...,
        # default="4000",
        description="Virtual machine id",
        pattern=r"^[0-9]+$"
    )

    # quick classic fields

    iface_model: str | None = Field(
        description="Interface model-  virtio, e1000, rtl8139",
        pattern=r"^[A-Za-z0-9._-]+$"
    )

    iface_bridge: str | None = Field(
        description="Bridge name for interface - vmbr0, vmbr142",
        pattern=r"^[A-Za-z0-9._-]+$"
    )

    vm_vmnet_id: int | None = Field(
        description="Network device index - 0, 1, 2, ..."
    )

    #### below fields to test :

    iface_trunks: bool | None = Field(
        description="Enable trunk - allow multiple vlan on interface"
    )

    iface_tag: int | None = Field(
        description="VLAN tag id"
    )

    iface_rate: float | None = Field(
        description="Limit bandwith - Mbps - 0 to x"
    )

    iface_queues: int | None = Field(
        description="Allocated amount allocated tx/rx on interface"
    )

    iface_mtu: int | None = Field(
        description="MTU"
    )

    iface_macaddr: str | None = Field(
        description="MAC address - hexa format",
        pattern = r'^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$'
    )

    iface_link_down: bool | None = Field(
        description="Force to set down the interface"
    )

    iface_firewall: bool | None = Field(
        description="Apply firewall rules on this interface"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "storage_name": "local",
                "as_json": True,
                #
                "vm_id": "1000",
                "vm_vmnet_id": "1",
                "iface_model": "virtio",
                "iface_bridge": "vmbr142",
            }
        }
    }


class VmNetworkAddItemReply(BaseModel):

    action: Literal["vm_DeleteIptablesRule_usage"]
    source: Literal["proxmox"]
    proxmox_node: str
    ##
    # vm_id: int = Field(..., ge=1)
    vm_id: str
    vm_fw_pos: int

class VmNetworkAddReply(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[VmNetworkAddItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "network_add_interfaces_vm",
                        "source": "proxmox",
                        "proxmox_node": "px-testing",
                        ##
                        "vm_id": "1000",
                        "iface_model": "virtio",
                    }
                ]
            }
        }
    }


# ---------------------------------------------------------------------------
# VM — Delete Network Interface
# ---------------------------------------------------------------------------

class VmNetworkDeleteRequest(BaseModel):

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

    vm_id: str = Field(
        ...,
        # default="4000",
        description="Virtual machine id",
        pattern=r"^[0-9]+$"
    )

    vm_vmnet_id: int | None = Field(
        description="Network device index - 0, 1, 2, ..."
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "storage_name": "local",
                "as_json": True,
#
                "vm_id":"1000",
                "vm_vmnet_id":1,

            }
        }
    }


class VmNetworkDeleteItemReply(BaseModel):

    action: Literal["vm_DeleteIptablesRule_usage"]
    source: Literal["proxmox"]
    proxmox_node: str
    ##
    # vm_id: int = Field(..., ge=1)
    vm_id: str
    vm_fw_pos: int

class VmNetworkDeleteReply(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[VmNetworkDeleteItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "network_delete_interfaces_vm",
                        "source": "proxmox",
                        "proxmox_node": "px-testing",
                        ##
                        "vm_id": "1000",
                        "iface_model": "virtio"

                     }
                ]
            }
        }
    }


# ---------------------------------------------------------------------------
# VM — List Network Interfaces
# ---------------------------------------------------------------------------

class VmNetworkListRequest(BaseModel):

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

    vm_id: str = Field(
        ...,
        # default="4000",
        description="Virtual machine id",
        pattern=r"^[0-9]+$"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "as_json": True,
                "vm_id": "1001",
            }
        }
    }


class VmNetworkListItemReply(BaseModel):

    action: Literal["vm_DeleteIptablesRule_usage"]
    source: Literal["proxmox"]
    proxmox_node: str
    ##
    # vm_id: int = Field(..., ge=1)
    vm_id: str

class VmNetworkListReply(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[VmNetworkListItemReply]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        "action": "network_list_interfaces_vm",
                        "source": "proxmox",
                        "proxmox_node": "px-testing",
                        ##
                        "vm_id": "1000",
                        "vm_network_bridge": "vmbr0",
                        "vm_network_device": "net0",
                        "vm_network_mac": "AA:BB:CC:DD:EE:FF",
                        "vm_network_type": "virtio"

                    }
                ]
            }
        }
    }


# ---------------------------------------------------------------------------
# Backward compatibility -- old names used by current routes
# ---------------------------------------------------------------------------

# network/node_name/add_network.py
Request_ProxmoxNetwork_WithNodeName_AddNetworkInterface = NodeNetworkAddRequest
Reply_ProxmoxNetwork_WithNodeName_AddNetworkInterfaceItem = NodeNetworkAddItemReply
Reply_ProxmoxNetwork_WithNodeName_AddNetworkInterface = NodeNetworkAddReply

# network/node_name/delete_network.py
Request_ProxmoxNetwork_WithNodeName_DeleteInterface = NodeNetworkDeleteRequest
Reply_ProxmoxNetwork_WithNodeName_DeleteInterfaceItem = NodeNetworkDeleteItemReply
Reply_ProxmoxNetwork_WithNodeName_DeleteInterface = NodeNetworkDeleteReply

# network/node_name/list_network.py
Request_ProxmoxNetwork_WithNodeName_ListInterface = NodeNetworkListRequest
Reply_ProxmoxNetwork_WithNodeName_ListInterfaceItem = NodeNetworkListItemReply
Reply_ProxmoxNetwork_WithNodeName_ListInterface = NodeNetworkListReply

# network/vm_id/add_network.py
Request_ProxmoxNetwork_WithVmId_AddNetwork = VmNetworkAddRequest
Reply_ProxmoxNetwork_WithVmId_AddNetworkInterfaceItem = VmNetworkAddItemReply
Reply_ProxmoxNetwork_WithVmId_AddNetworkInterface = VmNetworkAddReply

# network/vm_id/delete_network.py
Request_ProxmoxNetwork_WithVmId_DeleteNetwork = VmNetworkDeleteRequest
Reply_ProxmoxNetwork_WithVmId_DeleteNetworkInterfaceItem = VmNetworkDeleteItemReply
Reply_ProxmoxNetwork_WithVmId_DeleteNetworkInterface = VmNetworkDeleteReply

# network/vm_id/list_network.py
Request_ProxmoxNetwork_WithVmId_ListNetwork = VmNetworkListRequest
Reply_ProxmoxNetwork_WithVmId_ListNetworkInterfaceItem = VmNetworkListItemReply
Reply_ProxmoxNetwork_WithVmId_ListNetworkInterface = VmNetworkListReply
