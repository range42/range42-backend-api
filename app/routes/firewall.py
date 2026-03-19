"""Consolidated firewall routes.

Replaces: app/routes/v0/proxmox/firewall/*.py
"""

import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.runner import run_playbook_core
from app.extract_actions import extract_action_results
from app.utils.vm_id_name_resolver import resolv_id_to_vm_name
from app import utils

from app.schemas.proxmox.firewall.list_iptables_alias import Request_ProxmoxFirewall_ListIptablesAlias, Reply_ProxmoxFirewallWithStorageName_ListIptablesAlias
from app.schemas.proxmox.firewall.add_iptable_alias import Request_ProxmoxFirewall_AddIptablesAlias, Reply_ProxmoxFirewallWithStorageName_AddIptablesAlias
from app.schemas.proxmox.firewall.delete_iptables_alias import Request_ProxmoxFirewall_DeleteIptablesAlias, Reply_ProxmoxFirewallWithStorageName_DeleteIptablesAlias
from app.schemas.proxmox.firewall.list_iptables_rules import Request_ProxmoxFirewall_ListIptablesRules, Reply_ProxmoxFirewallWithStorageName_ListIptablesRules
from app.schemas.proxmox.firewall.apply_iptables_rules import Request_ProxmoxFirewall_ApplyIptablesRules, Reply_ProxmoxFirewallWithStorageName_ApplyIptablesRules
from app.schemas.proxmox.firewall.delete_iptables_rule import Request_ProxmoxFirewall_DeleteIptablesRule
from app.schemas.proxmox.firewall.enable_firewall_vm import Request_ProxmoxFirewall_EnableFirewallVm, Reply_ProxmoxFirewallWithStorageName_EnableFirewallVm
from app.schemas.proxmox.firewall.disable_firewall_vm import Request_ProxmoxFirewall_DistableFirewallVm, Reply_ProxmoxFirewallWithStorageName_DisableFirewallVm
from app.schemas.proxmox.firewall.enable_firewall_node import Request_ProxmoxFirewall_EnableFirewallNode, Reply_ProxmoxFirewallWithStorageName_EnableFirewallNode
from app.schemas.proxmox.firewall.disable_firewall_node import Request_ProxmoxFirewall_DistableFirewallNode, Reply_ProxmoxFirewallWithStorageName_DisableFirewallNode
from app.schemas.proxmox.firewall.enable_firewall_dc import Request_ProxmoxFirewall_EnableFirewallDc, Reply_ProxmoxFirewallWithStorageName_EnableFirewallDc
from app.schemas.proxmox.firewall.disable_firewall_dc import Request_ProxmoxFirewall_DisableFirewallDc, Reply_ProxmoxFirewallWithStorageName_DisableFirewallDc

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT_DIR")).resolve()
INVENTORY_NAME = "hosts"
PLAYBOOK_SRC = PROJECT_ROOT / "playbooks" / "generic.yml"

router = APIRouter()


def _run_fw(req, action: str, extravars: dict) -> JSONResponse:
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


# --- Alias routes ---

@router.post(path="/vm/alias/list", summary="List VM firewall aliases", description="List firewall aliases for a specific virtual machine", tags=["proxmox - firewall"], response_model=Reply_ProxmoxFirewallWithStorageName_ListIptablesAlias, response_description="Details of the VM firewall aliases")
def proxmox_vm_alias_list(req: Request_ProxmoxFirewall_ListIptablesAlias):
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id:
        extravars["vm_id"] = req.vm_id
    return _run_fw(req, "firewall_vm_list_iptables_alias", extravars)


@router.post(path="/vm/alias/add", summary="Add a firewall alias", description="Add a new alias to the Proxmox firewall - IPs, subnets/networks, hostnames", tags=["proxmox - firewall"], response_model=Reply_ProxmoxFirewallWithStorageName_AddIptablesAlias, response_description="Information about the created firewall alias")
def proxmox_firewall_vm_alias_add(req: Request_ProxmoxFirewall_AddIptablesAlias):
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id
    if req.vm_fw_alias_name is not None:
        extravars["vm_fw_alias_name"] = req.vm_fw_alias_name
    if req.vm_fw_alias_cidr is not None:
        extravars["vm_fw_alias_cidr"] = req.vm_fw_alias_cidr
    if req.vm_fw_alias_comment is not None:
        extravars["vm_fw_alias_comment"] = req.vm_fw_alias_comment
    return _run_fw(req, "firewall_vm_add_iptables_alias", extravars)


@router.delete(path="/vm/alias/delete", summary="Delete a firewall alias", description="Remove an existing alias from the proxmox firewall", tags=["proxmox - firewall"], response_model=Reply_ProxmoxFirewallWithStorageName_DeleteIptablesAlias, response_description="Details of the deleted firewall alias")
def proxmox_firewall_vm_alias_delete(req: Request_ProxmoxFirewall_DeleteIptablesAlias):
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id
    if req.vm_fw_alias_name is not None:
        extravars["vm_fw_alias_name"] = req.vm_fw_alias_name
    return _run_fw(req, "firewall_vm_delete_iptables_alias", extravars)


# --- Rules routes ---

@router.post(path="/vm/rules/list", summary="List VM firewall rules", description="List firewall rules for a specific virtual machine", tags=["proxmox - firewall"], response_model=Reply_ProxmoxFirewallWithStorageName_ListIptablesRules, response_description="Details of the VM firewall rules")
def proxmox_vm_rules_list(req: Request_ProxmoxFirewall_ListIptablesRules):
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id:
        extravars["vm_id"] = req.vm_id
    return _run_fw(req, "firewall_vm_list_iptables_rule", extravars)


@router.post(path="/vm/rules/apply", summary="Apply firewall rules", description="Apply the received firewall rules to the proxmox firewall", tags=["proxmox - firewall"], response_model=Reply_ProxmoxFirewallWithStorageName_ApplyIptablesRules, response_description="Details of the applied firewall rules")
def proxmox_firewall_vm_rules_add(req: Request_ProxmoxFirewall_ApplyIptablesRules):
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id
    for field in ("vm_fw_action", "vm_fw_dport", "vm_fw_enable", "vm_fw_proto", "vm_fw_type", "vm_fw_log", "vm_fw_iface", "vm_fw_source", "vm_fw_dest", "vm_fw_sport", "vm_fw_comment", "vm_fw_pos"):
        val = getattr(req, field, None)
        if val is not None:
            extravars[field] = val
    return _run_fw(req, "firewall_vm_apply_iptables_rule", extravars)


@router.delete(path="/vm/rules/delete", summary="Delete a firewall rule", description="Remove an existing rule from the proxmox firewall configuration", tags=["proxmox - firewall"], response_model=Request_ProxmoxFirewall_DeleteIptablesRule, response_description="Details of the deleted firewall rule.")
def proxmox_firewall_vm_rules_delete(req: Request_ProxmoxFirewall_DeleteIptablesRule):
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id
    if req.vm_fw_pos is not None:
        extravars["vm_fw_pos"] = req.vm_fw_pos
    return _run_fw(req, "firewall_vm_delete_iptables_rule", extravars)


# --- VM enable/disable ---

@router.post(path="/vm/enable", summary="Enable VM firewall", description="Enable the proxmox firewall for a specific virtual machine", tags=["proxmox - firewall"], response_model=Reply_ProxmoxFirewallWithStorageName_EnableFirewallVm, response_description="Details of the enabled VM firewall")
def proxmox_firewall_vm_enable(req: Request_ProxmoxFirewall_EnableFirewallVm):
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id:
        extravars["vm_id"] = req.vm_id
    extravars["vm_name"] = resolv_id_to_vm_name(extravars["proxmox_node"], extravars["vm_id"])
    return _run_fw(req, "firewall_vm_enable", extravars)


@router.post(path="/vm/disable", summary="Disable VM firewall", description="Disable the proxmox firewall for a specific virtual machine", tags=["proxmox - firewall"], response_model=Reply_ProxmoxFirewallWithStorageName_DisableFirewallVm, response_description="Details of the disabled VM firewall")
def proxmox_firewall_vm_disable(req: Request_ProxmoxFirewall_DistableFirewallVm):
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id:
        extravars["vm_id"] = req.vm_id
    extravars["vm_name"] = resolv_id_to_vm_name(extravars["proxmox_node"], extravars["vm_id"])
    return _run_fw(req, "firewall_vm_disable", extravars)


# --- Node enable/disable ---

@router.post(path="/node/enable", summary="Enable node firewall", description="Enable the proxmox firewall on a specific node", tags=["proxmox - firewall"], response_model=Reply_ProxmoxFirewallWithStorageName_EnableFirewallNode, response_description="Details of the enabled node firewall")
def proxmox_firewall_node_enable(req: Request_ProxmoxFirewall_EnableFirewallNode):
    extravars = {"proxmox_node": req.proxmox_node}
    return _run_fw(req, "firewall_node_enable", extravars)


@router.post(path="/node/disable", summary="Disable node firewall", description="Disable the proxmox firewall on a specific node", tags=["proxmox - firewall"], response_model=Reply_ProxmoxFirewallWithStorageName_DisableFirewallNode, response_description="Details of the disabled node firewall")
def proxmox_firewall_node_disable(req: Request_ProxmoxFirewall_DistableFirewallNode):
    extravars = {"proxmox_node": req.proxmox_node}
    return _run_fw(req, "firewall_node_disable", extravars)


# --- Datacenter enable/disable ---

@router.post(path="/datacenter/enable", summary="Enable datacenter firewall", description="Enable the proxmox firewall at the datacenter level", tags=["proxmox - firewall"], response_model=Reply_ProxmoxFirewallWithStorageName_EnableFirewallDc, response_description="Details of the enabled datacenter firewall")
def proxmox_firewall_dc_enable(req: Request_ProxmoxFirewall_EnableFirewallDc):
    extravars = {}
    if req.proxmox_api_host:
        extravars["proxmox_api_host"] = req.proxmox_api_host
    return _run_fw(req, "firewall_dc_enable", extravars)


@router.post(path="/datacenter/disable", summary="Disable datacenter firewall", description="Disable the proxmox firewall at the datacenter level", tags=["proxmox - firewall"], response_model=Reply_ProxmoxFirewallWithStorageName_DisableFirewallDc, response_description="Details of the disabled datacenter firewall")
def proxmox_firewall_dc_disable(req: Request_ProxmoxFirewall_DisableFirewallDc):
    extravars = {}
    if req.proxmox_api_host:
        extravars["proxmox_api_host"] = req.proxmox_api_host
    return _run_fw(req, "firewall_dc_disable", extravars)
