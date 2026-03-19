"""Consolidated debug routes.

Endpoints
---------
- ``POST /v0/admin/debug/ping`` -- Ansible ping connectivity check.
- ``POST /v0/admin/debug/func_test`` -- Temporary test function.
"""

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.core.runner import run_playbook_core
from app.schemas.debug import Request_DebugPing
from app.utils.vm_id_name_resolver import *

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT_DIR")).resolve()
PLAYBOOK_SRC = PROJECT_ROOT / "playbooks" / "ping.yml"
INVENTORY_SRC = PROJECT_ROOT / "inventory" / "hosts.yml"

router = APIRouter()


@router.post(
    path="/ping",
    summary="Run Ansible ping utility",
    description="This endpoint runs the Ansible ping module to check connectivity with target hosts.",
    tags=["runner"],
)
def debug_ping(req: Request_DebugPing):
    """Run Ansible ping to check connectivity with target hosts.

    :param req: Request body with ``hosts`` and optional ``proxmox_node``.
    :returns: JSON with ``rc`` and ``log_multiline``.
    """
    if not PLAYBOOK_SRC.exists():
        raise HTTPException(status_code=400, detail=f":: MISSING PLAYBOOK : {PLAYBOOK_SRC}")
    if not INVENTORY_SRC.exists():
        raise HTTPException(status_code=400, detail=f":: MISSING INVENTORY : {INVENTORY_SRC}")

    extravars = {}
    if req.proxmox_node:
        extravars["proxmox_node"] = req.proxmox_node
    if not extravars:
        extravars = None

    rc, events, log_plain, log_ansi = run_playbook_core(
        PLAYBOOK_SRC, INVENTORY_SRC, limit=req.hosts, extravars=extravars,
    )

    payload = {"rc": rc, "log_multiline": log_plain.splitlines()}
    return JSONResponse(payload, status_code=200 if rc == 0 else 500)


@router.post(
    path="/func_test",
    summary="temp stuff",
    description="_testing - tmp ",
    tags=["__tmp_testing"],
)
def debug_func_test():
    """Temporary test function for development.

    :returns: None (debug only).
    """
    if not PLAYBOOK_SRC.exists():
        raise HTTPException(status_code=400, detail=f":: MISSING PLAYBOOK : {PLAYBOOK_SRC}")
    if not INVENTORY_SRC.exists():
        raise HTTPException(status_code=400, detail=f":: MISSING INVENTORY : {INVENTORY_SRC}")

    out = resolv_id_to_vm_name("px-testing", 1000)
    logger.debug("GOT: %s", out["vm_name"])
