
from typing import List
from pydantic import BaseModel, Field

# from enum import Enum
# from typing import Optional, Literal, List
# from pydantic import BaseModel, Field, NonNegativeInt

#### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### ####


class Request_DebugPing(BaseModel):

    hosts: str | None = Field(
        default="all",
        description="Targeted ansible hosts",
        pattern=r"^[A-Za-z0-9\._-]+$"
    )

    proxmox_node: str | None = Field(
        default="px-testing",
        description="Proxmox node name",
        pattern=r"^[A-Za-z0-9_-]+$"
    )

    as_json: bool | None = Field(
        default=False,
        description="If true : JSON output else : raw output"
    )

# #### #### #### #### #### #### #### #### #### #### #### #### #### ####
# #### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Reply_DebugPing(BaseModel):

    rc: int = Field(
        ..., # mandatory field.
        description="Return code of the job (0 = success, >0 = error/warning)"
    )
    log_multiline: List[str] = Field(
        ..., # mandatory field.
        description="Execution log as a list of lines (chronological order)"
    )

    model_config = {
        "json_schema_extra": {
            "something": {
                "rc": 0,
                "log_multiline": [
                    "PLAY [debug - ping targeted host/group] ****************************************",
                    "",
                    "TASK [ansible.builtin.ping] ****************************************************",
                    "ok: [something-1]",
                    "",
                    "PLAY RECAP *********************************************************************",
                    "something-1 : ok=1 changed=0 unreachable=0 failed=0 skipped=0 rescued=0 ignored=0"
                ]
            }
        }
    }

