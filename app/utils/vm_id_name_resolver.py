"""VM ID to name resolution.

Provides :func:`resolv_id_to_vm_name`, which queries the Proxmox VM list
via Ansible and returns the ``vm_id`` / ``vm_name`` pair for a given VM ID.
Used by routes that need the VM name for operations like delete, clone,
and snapshot management.
"""

from  pathlib import Path
import os, json, logging
from fastapi import HTTPException

from app.core.runner import run_playbook_core
from app.core.extractor import extract_action_results

logger = logging.getLogger(__name__)


def hack_same_vm_id(a, b) -> bool:
    """Compare two VM IDs that may be int or str.

    Attempts integer comparison first, falls back to string comparison.

    :param a: First VM ID value.
    :param b: Second VM ID value.
    :returns: ``True`` if the IDs are equal after type coercion.
    :rtype: bool
    """

    try:
        return int(a) == int(b)

    except (TypeError, ValueError):
        return str(a) == str(b)

def resolv_id_to_vm_name(proxmox_node: str, target_vm_id: str) -> dict:
    """Resolve a VM ID to its name by querying the Proxmox VM list.

    Runs the ``vm_list`` action via Ansible, iterates over the results,
    and returns the matching ``vm_id`` / ``vm_name`` pair.

    :param proxmox_node: The Proxmox node name to query.
    :type proxmox_node: str
    :param target_vm_id: The VM ID to look up.
    :type target_vm_id: str
    :returns: Dict with ``"vm_id"`` and ``"vm_name"`` keys.
    :rtype: dict
    :raises HTTPException: 500 if the VM ID is not found in the results
        or the result data cannot be parsed.
    """

    PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT_DIR")).resolve()
    PLAYBOOK_SRC = PROJECT_ROOT / "playbooks" / "generic.yml"
    INVENTORY_SRC = PROJECT_ROOT / "inventory" / "hosts.yml"

    logger.debug("PROJECT_ROOT: %s", PROJECT_ROOT)
    logger.debug("PLAYBOOK_SRC: %s", PLAYBOOK_SRC)
    logger.debug("INVENTORY_SRC: %s", INVENTORY_SRC)

    if not PLAYBOOK_SRC.exists():
        err = f":: err - MISSING PLAYBOOK : {PLAYBOOK_SRC}"
        logger.error("Missing playbook: %s", PLAYBOOK_SRC)

    if not INVENTORY_SRC.exists():
        err = f":: err - MISSING INVENTORY : {INVENTORY_SRC}"
        logger.error("Missing inventory: %s", INVENTORY_SRC)

    extravars = {}
    extravars["proxmox_vm_action"] = "vm_list"

    ####

    rc, events, log_plain, log_ansi = run_playbook_core(
        PLAYBOOK_SRC,
        INVENTORY_SRC,
        extravars=extravars,
        quiet=True,
        # limit=extravars["hosts"],
        # limit=req.hosts,
    )

    action = extravars["proxmox_vm_action"]
    action_result = extract_action_results(events, action)

    ####

    # cross check json / py object
    if isinstance(action_result, str):

        try:
            data = json.loads(action_result)

        except json.JSONDecodeError as e:
            err = f":: err - INVALID actions_results JSONS"
            logger.error("Invalid action_results JSON")
            raise HTTPException(status_code=500, detail=err)

    else:
        data = action_result

    for outer in data: # first []

        if not isinstance(outer, list):
            continue

        for item in outer: # second[]

            # if isinstance(item, dict) and str(item.get("vm_id")) == target_vm_id:
            # if isinstance(item, dict) and item.get("vm_id") == target_vm_id:
            if isinstance(item, dict) and hack_same_vm_id(item.get("vm_id"), target_vm_id): # hacky way - should be fixed.

                logger.debug("Matched VM — vm_id: %s, vm_name: %s", item.get("vm_id"), item.get("vm_name"))

                return {
                    "vm_id": item.get("vm_id"),
                    "vm_name": item.get("vm_name"),
                }

    # return None

    err = f":: err - vm_id NOT FOUND"
    logger.error("vm_id not found: %s", target_vm_id)
    raise HTTPException(status_code=500, detail=err)
