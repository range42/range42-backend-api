
from typing import Literal
from pydantic import BaseModel, Field

#
# ISSUE - #20
#

class Request_BundlesCoreLinuxUbuntuInstall_BasicPackages(BaseModel):

    proxmox_node: str = Field(
        ...,
        # default= "px-testing",
        description = "Proxmox node name",
        pattern=r"^[A-Za-z0-9-]*$"
    )

    hosts: str = Field(
        ...,
        description= "Hosts or groups",
        pattern = r"^[a-zA-Z0-9._:-]+$"
    )

    # as_json: bool = Field(
    #     default=True,
    #     description="If true : JSON output else : raw output"
    # )
    #

    ####

    install_package_basics : bool =  Field(
    ...,
    description="",
    )

    install_package_firewalls : bool =  Field(
    ...,
    description="",
    )

    install_package_docker : bool =  Field(
    ...,
    description="",
    )

    install_package_docker_compose: bool =  Field(
    ...,
    description="",
    )

    install_package_utils_json : bool =  Field(
    ...,
    description="",
    )

    install_package_utils_network : bool =  Field(
    ...,
    description="",
    )

    ####

    install_ntpclient_and_update_time: bool =  Field(
    ...,
        description="",
    )

    packages_cleaning: bool =  Field(
    ...,
        description="",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "proxmox_node": "px-testing",
                "hosts": "r42.vuln-box-00",
                # "as_json": True,
                #
                "install_package_basics" : True,
                "install_package_firewalls"  : False,
                "install_package_docker"  : False,
                "install_package_docker_compose" : False,
                "install_package_utils_json"  : False,
                "install_package_utils_network" : False,
                "install_ntpclient_and_update_time" : True,
                "packages_cleaning" : True,

            }
        }
    }

#### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### ####

class Reply_BundlesCoreLinuxUbuntuInstall_BasicPackagesItem(BaseModel):

    # action: Literal["vm_get_config"]
    # source: Literal["proxmox"]
    proxmox_node: str
    # vm_id: int = Field(..., ge=1)
    # vm_name: str
    raw_data: str = Field(..., description="Raw string returned by proxmox")


class Reply_BundlesCoreLinuxUbuntuInstall_BasicPackages(BaseModel):

    rc: int = Field(0, description="RETURN code (0 = OK)")
    result: list[Reply_BundlesCoreLinuxUbuntuInstall_BasicPackagesItem]

    model_config = {
        "json_schema_extra": {
            "example": {
                "rc": 0,
                "result": [
                    {
                        # "action": "vm_get_config",
                        # "proxmox_node": "px-testing",
                        # ...
                        # "source": "proxmox",
                        # "vm_id": "1000"
                    }
                ]
            }
        }
    }


#### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### ####
