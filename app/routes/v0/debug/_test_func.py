
from fastapi import APIRouter, HTTPException
from app.utils.vm_id_name_resolver import *

import os

#
# ISSUE - #18
#


router = APIRouter()

# PROJECT_ROOT = Path(__file__).resolve().parents[4]
PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT_DIR")).resolve()
PLAYBOOK_SRC = PROJECT_ROOT / "playbooks" / "ping.yml"
INVENTORY_SRC = PROJECT_ROOT / "inventory" / "hosts.yml"


debug = 0

@router.post(
    path="/func_test",
    summary="temp stuff",
    description="_testing - tmp ",
    tags=["__tmp_testing"],
)

def debug_ping():

    if debug ==1:
        # print("::  REQUEST ::", req.dict())
        print(f":: PROJECT_ROOT  :: {PROJECT_ROOT} ")
        print(f":: PLAYBOOK_SRC  :: {PLAYBOOK_SRC} ")
        print(f":: INVENTORY_SRC :: {INVENTORY_SRC} ")

    if not PLAYBOOK_SRC.exists():
        raise HTTPException(status_code=400, detail=f":: MISSING PLAYBOOK : {PLAYBOOK_SRC}")
    if not INVENTORY_SRC.exists():
        raise HTTPException(status_code=400, detail=f":: MISSING INVENTORY : {INVENTORY_SRC}")

    #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####
    #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####
    #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####

    print("------------------------------")
    # out = resolv_id_to_vm_name("px-testing", 29999) # must raise error 500
    out = resolv_id_to_vm_name("px-testing", 1000)
    print ("GOT :::")
    print (out["vm_name"])

    print("------------------------------")
    out = resolv_id_to_vm_name("px-testing", "1000")
    print ("GOT :::")
    print (out["vm_name"])
    print("------------------------------")
