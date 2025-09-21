
from typing import Literal, List
from pydantic import BaseModel, Field


class Request_BundlesCoreProxmoxConfigureDefaultVms_StartStopPauseResumeAdminVulnStudentVms(BaseModel):

    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description = "Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$"
    )

    as_json: bool = Field(
        default=True,
        description="If true : JSON output else : raw output"
    )
    #

    # vm_id: str = Field(
    #     ...,
    #     # default="1000",
    #     description="Virtual machine id",
    #     pattern=r"^[0-9]+$"
    # )

    # vm_ids: List[str] = Field(
    #     ...,
    #     # default="1000",
    #     description="Virtual machine id",
    #     min_items=1,
    #     # pattern=r"^[0-9]+$"
    # )


    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "as_json": True,

            }
        }
    }
