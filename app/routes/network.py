"""Consolidated network routes.

Replaces: app/routes/v0/proxmox/network/vm/*.py, network/node/*.py
"""

import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.runner import run_playbook_core
from app.extract_actions import extract_action_results
from app import utils

from app.schemas.proxmox.network.vm_id.add_network import Request_ProxmoxNetwork_WithVmId_AddNetwork, Reply_ProxmoxNetwork_WithVmId_AddNetworkInterface
from app.schemas.proxmox.network.vm_id.delete_network import Request_ProxmoxNetwork_WithVmId_DeleteNetwork, Reply_ProxmoxNetwork_WithVmId_DeleteNetworkInterface
from app.schemas.proxmox.network.vm_id.list_network import Request_ProxmoxNetwork_WithVmId_ListNetwork, Reply_ProxmoxNetwork_WithVmId_ListNetworkInterface
from app.schemas.proxmox.network.node_name.add_network import Request_ProxmoxNetwork_WithNodeName_AddNetworkInterface, Reply_ProxmoxNetwork_WithNodeName_AddNetworkInterface
from app.schemas.proxmox.network.node_name.delete_network import Request_ProxmoxNetwork_WithNodeName_DeleteInterface, Reply_ProxmoxNetwork_WithNodeName_DeleteInterface
from app.schemas.proxmox.network.node_name.list_network import Request_ProxmoxNetwork_WithNodeName_ListInterface, Reply_ProxmoxNetwork_WithNodeName_ListInterface

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT_DIR")).resolve()
INVENTORY_NAME = "hosts"
PLAYBOOK_SRC = PROJECT_ROOT / "playbooks" / "generic.yml"

router = APIRouter()


def _run_net(req, action: str, extravars: dict) -> JSONResponse:
    extravars["proxmox_vm_action"] = action
    extravars["hosts"] = "proxmox"
    if not PLAYBOOK_SRC.exists():
        raise HTTPException(status_code=400, detail=f":: err - MISSING PLAYBOOK : {PLAYBOOK_SRC}")
    inventory = utils.resolve_inventory(INVENTORY_NAME)
    rc, events, log_plain, _ = run_playbook_core(PLAYBOOK_SRC, inventory, limit=extravars["hosts"], extravars=extravars)
    if req.as_json:
        payload = {"rc": rc, "result": extract_action_results(events, action)}
    else:
        payload = {"rc": rc, "log_multiline": log_plain.splitlines()}
    return JSONResponse(payload, status_code=200 if rc == 0 else 500)


# --- VM network routes ---

@router.post(path="/vm/add", summary="Add VM network interface", description="Create and attach a new network interface to a Proxmox VM.", tags=["proxmox - network - vm"], response_model=Reply_ProxmoxNetwork_WithVmId_AddNetworkInterface, response_description="Information about the added network interface.")
def proxmox_network_vm_add_interface(req: Request_ProxmoxNetwork_WithVmId_AddNetwork):
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id
    for field in ("iface_model", "iface_bridge", "vm_vmnet_id", "iface_trunks", "iface_tag", "iface_rate", "iface_queues", "iface_mtu", "iface_macaddr", "iface_link_down", "iface_firewall"):
        val = getattr(req, field, None)
        if val is not None:
            extravars[field] = val
    return _run_net(req, "network_add_interfaces_vm", extravars)


@router.post(path="/vm/delete", summary="Delete VM network interface", description="Remove a network interface from a Proxmox VM.", tags=["proxmox - network - vm"], response_model=Reply_ProxmoxNetwork_WithVmId_DeleteNetworkInterface, response_description="Information about the deleted network interface.")
def proxmox_network_vm_delete_interface(req: Request_ProxmoxNetwork_WithVmId_DeleteNetwork):
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id
    if req.vm_vmnet_id is not None:
        extravars["vm_vmnet_id"] = req.vm_vmnet_id
    return _run_net(req, "network_delete_interfaces_vm", extravars)


@router.post(path="/vm/list", summary="List VM network interfaces", description="Retrieve all network interfaces attached to a Proxmox VM.", tags=["proxmox - network - vm"], response_model=Reply_ProxmoxNetwork_WithVmId_ListNetworkInterface, response_description="List of VM network interfaces.")
def proxmox_network_vm_list_interface(req: Request_ProxmoxNetwork_WithVmId_ListNetwork):
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id
    return _run_net(req, "network_list_interfaces_vm", extravars)


# --- Node network routes ---

@router.post(path="/node/add", summary="Add node network interface", description="Create and attach a new network interface to a Proxmox node.", tags=["proxmox - network - node"], response_model=Reply_ProxmoxNetwork_WithNodeName_AddNetworkInterface, response_description="Information about the added network interface.")
def proxmox_network_node_add_interface(req: Request_ProxmoxNetwork_WithNodeName_AddNetworkInterface):
    extravars = {"proxmox_node": req.proxmox_node}
    for field in ("bridge_ports", "iface_name", "iface_type", "iface_autostart", "ip_address", "ip_netmask", "ip_gateway", "ovs_bridge"):
        val = getattr(req, field, None)
        if val is not None:
            extravars[field] = val
    return _run_net(req, "network_add_interfaces_node", extravars)


@router.post(path="/node/delete", summary="Delete node network interface", description="Remove a network interface from a Proxmox node.", tags=["proxmox - network - node"], response_model=Reply_ProxmoxNetwork_WithNodeName_DeleteInterface, response_description="Information about the deleted network interface.")
def proxmox_network_node_delete_interface(req: Request_ProxmoxNetwork_WithNodeName_DeleteInterface):
    extravars = {"proxmox_node": req.proxmox_node}
    if req.iface_name is not None:
        extravars["iface_name"] = req.iface_name
    return _run_net(req, "network_delete_interfaces_node", extravars)


# NOTE: original path has double slash "//node/list" - preserving exactly
@router.post(path="//node/list", summary="List node network interfaces", description="Retrieve all network interfaces configured on a Proxmox node.", tags=["proxmox - network - node"], response_model=Reply_ProxmoxNetwork_WithNodeName_ListInterface, response_description="List of node network interfaces.")
def proxmox_network_node_list_interface(req: Request_ProxmoxNetwork_WithNodeName_ListInterface):
    extravars = {"proxmox_node": req.proxmox_node}
    return _run_net(req, "network_list_interfaces_node", extravars)
