"""Consolidated firewall routes.

Endpoints
---------
- ``POST /v0/admin/proxmox/firewall/vm/alias/list`` -- List VM aliases.
- ``POST /v0/admin/proxmox/firewall/vm/alias/add`` -- Add a VM alias.
- ``DELETE /v0/admin/proxmox/firewall/vm/alias/delete`` -- Delete a VM alias.
- ``POST /v0/admin/proxmox/firewall/vm/rules/list`` -- List VM rules.
- ``POST /v0/admin/proxmox/firewall/vm/rules/apply`` -- Apply VM rules.
- ``DELETE /v0/admin/proxmox/firewall/vm/rules/delete`` -- Delete a VM rule.
- ``POST /v0/admin/proxmox/firewall/vm/enable`` -- Enable VM firewall.
- ``POST /v0/admin/proxmox/firewall/vm/disable`` -- Disable VM firewall.
- ``POST /v0/admin/proxmox/firewall/node/enable`` -- Enable node firewall.
- ``POST /v0/admin/proxmox/firewall/node/disable`` -- Disable node firewall.
- ``POST /v0/admin/proxmox/firewall/datacenter/enable`` -- Enable DC firewall.
- ``POST /v0/admin/proxmox/firewall/datacenter/disable`` -- Disable DC firewall.
"""

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app import utils
from app.core.extractor import extract_action_results
from app.core.logging import get_logger
from app.core.runner import run_playbook_core
from app.schemas.firewall import (
    Reply_ProxmoxFirewallWithStorageName_AddIptablesAlias,
    Reply_ProxmoxFirewallWithStorageName_ApplyIptablesRules,
    Reply_ProxmoxFirewallWithStorageName_DeleteIptablesAlias,
    Reply_ProxmoxFirewallWithStorageName_DisableFirewallDc,
    Reply_ProxmoxFirewallWithStorageName_DisableFirewallNode,
    Reply_ProxmoxFirewallWithStorageName_DisableFirewallVm,
    Reply_ProxmoxFirewallWithStorageName_EnableFirewallDc,
    Reply_ProxmoxFirewallWithStorageName_EnableFirewallNode,
    Reply_ProxmoxFirewallWithStorageName_EnableFirewallVm,
    Reply_ProxmoxFirewallWithStorageName_ListIptablesAlias,
    Reply_ProxmoxFirewallWithStorageName_ListIptablesRules,
    Request_ProxmoxFirewall_AddIptablesAlias,
    Request_ProxmoxFirewall_ApplyIptablesRules,
    Request_ProxmoxFirewall_DeleteIptablesAlias,
    Request_ProxmoxFirewall_DeleteIptablesRule,
    Request_ProxmoxFirewall_DisableFirewallDc,
    Request_ProxmoxFirewall_DistableFirewallNode,
    Request_ProxmoxFirewall_DistableFirewallVm,
    Request_ProxmoxFirewall_EnableFirewallDc,
    Request_ProxmoxFirewall_EnableFirewallNode,
    Request_ProxmoxFirewall_EnableFirewallVm,
    Request_ProxmoxFirewall_ListIptablesAlias,
    Request_ProxmoxFirewall_ListIptablesRules,
)
from app.utils.vm_id_name_resolver import resolv_id_to_vm_name

logger = get_logger(__name__)

PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT_DIR")).resolve()
INVENTORY_NAME = "hosts"
PLAYBOOK_SRC = PROJECT_ROOT / "playbooks" / "generic.yml"

router = APIRouter()


def _run_fw(req, action: str, extravars: dict) -> JSONResponse:
    extravars["proxmox_vm_action"] = action
    extravars["hosts"] = "proxmox"
    if not PLAYBOOK_SRC.exists():
        raise HTTPException(
            status_code=400, detail=f":: err - MISSING PLAYBOOK : {PLAYBOOK_SRC}"
        )
    inventory = utils.resolve_inventory(INVENTORY_NAME)
    rc, events, log_plain, _ = run_playbook_core(
        PLAYBOOK_SRC, inventory, limit=extravars["hosts"], extravars=extravars
    )
    if req.as_json:
        payload = {"rc": rc, "result": extract_action_results(events, action)}
    else:
        payload = {"rc": rc, "log_multiline": log_plain.splitlines()}
    return JSONResponse(payload, status_code=200 if rc == 0 else 500)


# --- Alias routes ---


@router.post(
    path="/vm/alias/list",
    summary="List VM firewall aliases",
    description="List firewall aliases for a specific virtual machine",
    tags=["proxmox - firewall"],
    response_model=Reply_ProxmoxFirewallWithStorageName_ListIptablesAlias,
    response_description="Details of the VM firewall aliases",
)
def proxmox_vm_alias_list(req: Request_ProxmoxFirewall_ListIptablesAlias):
    """List firewall aliases for a VM.

    :param req: Request body with ``proxmox_node`` and ``vm_id``.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id:
        extravars["vm_id"] = req.vm_id
    return _run_fw(req, "firewall_vm_list_iptables_alias", extravars)


@router.post(
    path="/vm/alias/add",
    summary="Add a firewall alias",
    description="Add a new alias to the Proxmox firewall - IPs, subnets/networks, hostnames",
    tags=["proxmox - firewall"],
    response_model=Reply_ProxmoxFirewallWithStorageName_AddIptablesAlias,
    response_description="Information about the created firewall alias",
)
def proxmox_firewall_vm_alias_add(req: Request_ProxmoxFirewall_AddIptablesAlias):
    """Add a firewall alias (IP, subnet, or hostname) for a VM.

    :param req: Request body with node, VM ID, alias name, CIDR, and comment.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
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


@router.delete(
    path="/vm/alias/delete",
    summary="Delete a firewall alias",
    description="Remove an existing alias from the proxmox firewall",
    tags=["proxmox - firewall"],
    response_model=Reply_ProxmoxFirewallWithStorageName_DeleteIptablesAlias,
    response_description="Details of the deleted firewall alias",
)
def proxmox_firewall_vm_alias_delete(req: Request_ProxmoxFirewall_DeleteIptablesAlias):
    """Delete a firewall alias from a VM.

    :param req: Request body with node, VM ID, and alias name.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id
    if req.vm_fw_alias_name is not None:
        extravars["vm_fw_alias_name"] = req.vm_fw_alias_name
    return _run_fw(req, "firewall_vm_delete_iptables_alias", extravars)


# --- Rules routes ---


@router.post(
    path="/vm/rules/list",
    summary="List VM firewall rules",
    description="List firewall rules for a specific virtual machine",
    tags=["proxmox - firewall"],
    response_model=Reply_ProxmoxFirewallWithStorageName_ListIptablesRules,
    response_description="Details of the VM firewall rules",
)
def proxmox_vm_rules_list(req: Request_ProxmoxFirewall_ListIptablesRules):
    """List firewall rules for a VM.

    :param req: Request body with ``proxmox_node`` and ``vm_id``.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id:
        extravars["vm_id"] = req.vm_id
    return _run_fw(req, "firewall_vm_list_iptables_rule", extravars)


@router.post(
    path="/vm/rules/apply",
    summary="Apply firewall rules",
    description="Apply the received firewall rules to the proxmox firewall",
    tags=["proxmox - firewall"],
    response_model=Reply_ProxmoxFirewallWithStorageName_ApplyIptablesRules,
    response_description="Details of the applied firewall rules",
)
def proxmox_firewall_vm_rules_add(req: Request_ProxmoxFirewall_ApplyIptablesRules):
    """Apply firewall rules to a VM.

    :param req: Request body with node, VM ID, and rule parameters.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id
    for field in (
        "vm_fw_action",
        "vm_fw_dport",
        "vm_fw_enable",
        "vm_fw_proto",
        "vm_fw_type",
        "vm_fw_log",
        "vm_fw_iface",
        "vm_fw_source",
        "vm_fw_dest",
        "vm_fw_sport",
        "vm_fw_comment",
        "vm_fw_pos",
    ):
        val = getattr(req, field, None)
        if val is not None:
            extravars[field] = val
    return _run_fw(req, "firewall_vm_apply_iptables_rule", extravars)


@router.delete(
    path="/vm/rules/delete",
    summary="Delete a firewall rule",
    description="Remove an existing rule from the proxmox firewall configuration",
    tags=["proxmox - firewall"],
    response_model=Request_ProxmoxFirewall_DeleteIptablesRule,
    response_description="Details of the deleted firewall rule.",
)
def proxmox_firewall_vm_rules_delete(req: Request_ProxmoxFirewall_DeleteIptablesRule):
    """Delete a firewall rule from a VM by position.

    :param req: Request body with node, VM ID, and rule position.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id is not None:
        extravars["vm_id"] = req.vm_id
    if req.vm_fw_pos is not None:
        extravars["vm_fw_pos"] = req.vm_fw_pos
    return _run_fw(req, "firewall_vm_delete_iptables_rule", extravars)


# --- VM enable/disable ---


@router.post(
    path="/vm/enable",
    summary="Enable VM firewall",
    description="Enable the proxmox firewall for a specific virtual machine",
    tags=["proxmox - firewall"],
    response_model=Reply_ProxmoxFirewallWithStorageName_EnableFirewallVm,
    response_description="Details of the enabled VM firewall",
)
def proxmox_firewall_vm_enable(req: Request_ProxmoxFirewall_EnableFirewallVm):
    """Enable the firewall on a VM.

    :param req: Request body with ``proxmox_node`` and ``vm_id``.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id:
        extravars["vm_id"] = req.vm_id
    extravars["vm_name"] = resolv_id_to_vm_name(
        extravars["proxmox_node"], extravars["vm_id"]
    )
    return _run_fw(req, "firewall_vm_enable", extravars)


@router.post(
    path="/vm/disable",
    summary="Disable VM firewall",
    description="Disable the proxmox firewall for a specific virtual machine",
    tags=["proxmox - firewall"],
    response_model=Reply_ProxmoxFirewallWithStorageName_DisableFirewallVm,
    response_description="Details of the disabled VM firewall",
)
def proxmox_firewall_vm_disable(req: Request_ProxmoxFirewall_DistableFirewallVm):
    """Disable the firewall on a VM.

    :param req: Request body with ``proxmox_node`` and ``vm_id``.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {"proxmox_node": req.proxmox_node}
    if req.vm_id:
        extravars["vm_id"] = req.vm_id
    extravars["vm_name"] = resolv_id_to_vm_name(
        extravars["proxmox_node"], extravars["vm_id"]
    )
    return _run_fw(req, "firewall_vm_disable", extravars)


# --- Node enable/disable ---


@router.post(
    path="/node/enable",
    summary="Enable node firewall",
    description="Enable the proxmox firewall on a specific node",
    tags=["proxmox - firewall"],
    response_model=Reply_ProxmoxFirewallWithStorageName_EnableFirewallNode,
    response_description="Details of the enabled node firewall",
)
def proxmox_firewall_node_enable(req: Request_ProxmoxFirewall_EnableFirewallNode):
    """Enable the firewall on a Proxmox node.

    :param req: Request body with ``proxmox_node``.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {"proxmox_node": req.proxmox_node}
    return _run_fw(req, "firewall_node_enable", extravars)


@router.post(
    path="/node/disable",
    summary="Disable node firewall",
    description="Disable the proxmox firewall on a specific node",
    tags=["proxmox - firewall"],
    response_model=Reply_ProxmoxFirewallWithStorageName_DisableFirewallNode,
    response_description="Details of the disabled node firewall",
)
def proxmox_firewall_node_disable(req: Request_ProxmoxFirewall_DistableFirewallNode):
    """Disable the firewall on a Proxmox node.

    :param req: Request body with ``proxmox_node``.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {"proxmox_node": req.proxmox_node}
    return _run_fw(req, "firewall_node_disable", extravars)


# --- Datacenter enable/disable ---


@router.post(
    path="/datacenter/enable",
    summary="Enable datacenter firewall",
    description="Enable the proxmox firewall at the datacenter level",
    tags=["proxmox - firewall"],
    response_model=Reply_ProxmoxFirewallWithStorageName_EnableFirewallDc,
    response_description="Details of the enabled datacenter firewall",
)
def proxmox_firewall_dc_enable(req: Request_ProxmoxFirewall_EnableFirewallDc):
    """Enable the firewall at the datacenter level.

    :param req: Request body with ``proxmox_api_host``.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {}
    if req.proxmox_api_host:
        extravars["proxmox_api_host"] = req.proxmox_api_host
    return _run_fw(req, "firewall_dc_enable", extravars)


@router.post(
    path="/datacenter/disable",
    summary="Disable datacenter firewall",
    description="Disable the proxmox firewall at the datacenter level",
    tags=["proxmox - firewall"],
    response_model=Reply_ProxmoxFirewallWithStorageName_DisableFirewallDc,
    response_description="Details of the disabled datacenter firewall",
)
def proxmox_firewall_dc_disable(req: Request_ProxmoxFirewall_DisableFirewallDc):
    """Disable the firewall at the datacenter level.

    :param req: Request body with ``proxmox_api_host``.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {}
    if req.proxmox_api_host:
        extravars["proxmox_api_host"] = req.proxmox_api_host
    return _run_fw(req, "firewall_dc_disable", extravars)
