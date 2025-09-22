
from typing import Literal, List
from pydantic import BaseModel, Field


class Request_BundlesCoreProxmoxConfigureDefaultVms_RevertSnapshotAdminVulnStudentVms(BaseModel):

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

    vm_snapshot_name: str | None = Field(
        default=None,
        description="Name of the snapshot to create",
        pattern=r"^[A-Za-z0-9_-]+$"
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
                "vm_snapshot_name": "default-snapshot-from-API-220925-1734",
                "as_json": True,

            }
        }
    }
