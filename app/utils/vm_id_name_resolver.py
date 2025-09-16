
from  pathlib import Path
import os, json, logging
from fastapi import HTTPException

from app.runner import  run_playbook_core # , extract_action_results
from app.json_extract import extract_action_results

debug =1


def hack_same_vm_id(a, b) -> bool:

    try:
        return int(a) == int(b)

    except (TypeError, ValueError):
        return str(a) == str(b)

def resolv_id_to_vm_name(proxmox_node: str, target_vm_id: str) -> dict:

    PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT_DIR")).resolve()
    PLAYBOOK_SRC = PROJECT_ROOT / "playbooks" / "generic.yml"
    INVENTORY_SRC = PROJECT_ROOT / "inventory" / "hosts.yml"

    if debug == 1:
        print(f":: PROJECT_ROOT  :: {PROJECT_ROOT} ")
        print(f":: PLAYBOOK_SRC  :: {PLAYBOOK_SRC} ")
        print(f":: INVENTORY_SRC :: {INVENTORY_SRC} ")

    if not PLAYBOOK_SRC.exists():
        err = f":: err - MISSING PLAYBOOK : {PLAYBOOK_SRC}"
        logging.error(err)

    if not INVENTORY_SRC.exists():
        err = f":: err - MISSING INVENTORY : {INVENTORY_SRC}"
        logging.error(err)

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
            logging.error(err)
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

                if debug ==1 :

                    print("=====================")
                    print( item.get("vm_id"))
                    print( item.get("vm_name"))
                    print("=====================")

                return {
                    "vm_id": item.get("vm_id"),
                    "vm_name": item.get("vm_name"),
                }

    # return None

    err = f":: err - vm_id NOT FOUND"
    logging.error(err)
    raise HTTPException(status_code=500, detail=err)




