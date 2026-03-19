"""Consolidated dynamic runner routes.

Endpoints
---------
- ``POST /v0/admin/run/bundles/{bundles_name}/run`` -- Run a named bundle.
- ``POST /v0/admin/run/scenarios/{scenario_name}/run`` -- Run a named scenario.
"""

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.runner import run_playbook_core
from app.schemas.debug import Request_DebugPing
from app import utils

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT_DIR")).resolve()
INVENTORY_NAME = "hosts"

router = APIRouter()


def _run_generic(req, name: str, resolver_fn) -> JSONResponse:
    checked_inventory = utils.resolve_inventory(INVENTORY_NAME)
    checked_playbook = resolver_fn(name, "public_github")

    extravars = {}
    if req.proxmox_node:
        extravars["proxmox_node"] = req.proxmox_node
    if not extravars:
        extravars = None

    rc, events, log_plain, _ = run_playbook_core(
        checked_playbook, checked_inventory, limit=req.hosts, extravars=extravars,
    )

    payload = {"rc": rc, "log_multiline": log_plain.splitlines()}
    return JSONResponse(payload, status_code=200 if rc == 0 else 500)


@router.post(
    path="/{bundles_name}/run",
    summary="Run bundles",
    description="Run generic bundles with default (and static) extras_vars ",
    tags=["runner"],
)
def run_bundle(bundles_name: str, req: Request_DebugPing):
    """Run a named bundle playbook from the external playbooks repository.

    :param bundles_name: Bundle path (e.g. ``"core/linux/ubuntu/install/docker"``).
    :param req: Request body with ``hosts`` and optional ``proxmox_node``.
    :returns: JSON with ``rc`` and ``log_multiline``.
    """
    return _run_generic(req, bundles_name, utils.resolve_bundles_playbook)


@router.post(
    path="/{scenario_name}/run",
    summary="Run scenario",
    description="Run generic scenario with default (and static) extras_vars ",
    tags=["runner"],
)
def run_scenario(scenario_name: str, req: Request_DebugPing):
    """Run a named scenario playbook from the external playbooks repository.

    :param scenario_name: Scenario path (e.g. ``"demo_lab"``).
    :param req: Request body with ``hosts`` and optional ``proxmox_node``.
    :returns: JSON with ``rc`` and ``log_multiline``.
    """
    return _run_generic(req, scenario_name, utils.resolve_scenarios_playbook)
