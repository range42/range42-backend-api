"""Consolidated storage routes.

Endpoints
---------
- ``POST /v0/admin/proxmox/storage/list`` -- List storage pools.
- ``POST /v0/admin/proxmox/storage/download_iso`` -- Download an ISO file.
- ``POST /v0/admin/proxmox/storage/storage_name/list_iso`` -- List ISOs in storage.
- ``POST /v0/admin/proxmox/storage/storage_name/list_template`` -- List templates.
"""

import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.core.runner import run_playbook_core
from app.core.extractor import extract_action_results
from app import utils

from app.schemas.storage import (
    Request_ProxmoxStorage_List, Reply_ProxmoxStorage_ListItem,
    Request_ProxmoxStorage_DownloadIso, Reply_ProxmoxStorage_DownloadIsoItem,
    Request_ProxmoxStorage_ListIso, Reply_ProxmoxStorageWithStorageName_ListIsoItem,
    Request_ProxmoxStorage_ListTemplate, Reply_ProxmoxStorageWithStorageName_ListTemplate,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT_DIR")).resolve()
INVENTORY_NAME = "hosts"
PLAYBOOK_SRC = PROJECT_ROOT / "playbooks" / "generic.yml"

# Two routers for different prefixes
storage_router = APIRouter()
storage_name_router = APIRouter()


def _run_storage(req, action: str, extravars: dict) -> JSONResponse:
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


# --- /v0/admin/proxmox/storage/ ---

@storage_router.post(path="/list", summary="Retrieve configuration of a VM", description="Returns the configuration details of the specified virtual machine (VM).", tags=["proxmox - storage"], response_model=Reply_ProxmoxStorage_ListItem, response_description="VM configuration details")
def proxmox_storage_list(req: Request_ProxmoxStorage_List):
    """List storage pools on the Proxmox node.

    :param req: Request body with ``proxmox_node`` and optional ``storage_name``.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {"proxmox_node": req.proxmox_node}
    if req.storage_name is not None:
        extravars["storage_name"] = req.storage_name
    return _run_storage(req, "storage_list", extravars)


@storage_router.post(path="/download_iso", summary="Retrieve configuration of a VM", description="Returns the configuration details of the specified virtual machine (VM).", tags=["proxmox - storage"], response_model=Reply_ProxmoxStorage_DownloadIsoItem, response_description="VM configuration details")
def proxmox_storage_download_iso(req: Request_ProxmoxStorage_DownloadIso):
    """Download an ISO file to a Proxmox storage pool.

    :param req: Request body with node, storage name, ISO URL, and metadata.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {"proxmox_node": req.proxmox_node}
    if req.proxmox_storage is not None:
        extravars["proxmox_storage"] = req.proxmox_storage
    if req.iso_file_content_type is not None:
        extravars["iso_file_content_type"] = req.iso_file_content_type
    if req.iso_file_name is not None:
        extravars["iso_file_name"] = req.iso_file_name
    if req.iso_url is not None:
        extravars["iso_url"] = req.iso_url
    return _run_storage(req, "storage_download_iso", extravars)


# --- /v0/admin/proxmox/storage/storage_name/ ---

@storage_name_router.post(path="/list_iso", summary="Retrieve configuration of a VM", description="Returns the configuration details of the specified virtual machine (VM).", tags=["proxmox - storage"], response_model=Reply_ProxmoxStorageWithStorageName_ListIsoItem, response_description="VM configuration details")
def proxmox_storage_with_storage_name_list_iso(req: Request_ProxmoxStorage_ListIso):
    """List ISO files in a named storage pool.

    :param req: Request body with ``proxmox_node`` and ``storage_name``.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {"proxmox_node": req.proxmox_node}
    if req.storage_name is not None:
        extravars["storage_name"] = req.storage_name
    return _run_storage(req, "storage_list_iso", extravars)


@storage_name_router.post(path="/list_template", summary="Retrieve configuration of a VM", description="Returns the configuration details of the specified virtual machine (VM).", tags=["proxmox - storage"], response_model=Reply_ProxmoxStorageWithStorageName_ListTemplate, response_description="VM configuration details")
def proxmox_storage_with_storage_name_list_template(req: Request_ProxmoxStorage_ListTemplate):
    """List VM templates in a named storage pool.

    :param req: Request body with ``proxmox_node`` and ``storage_name``.
    :returns: JSON with ``rc`` and either ``result`` or ``log_multiline``.
    """
    extravars = {"proxmox_node": req.proxmox_node}
    if req.storage_name is not None:
        extravars["storage_name"] = req.storage_name
    return _run_storage(req, "storage_list_template", extravars)
