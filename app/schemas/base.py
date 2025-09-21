
from typing import Optional, Literal
from pydantic import BaseModel, Field


class PingRequest(BaseModel):
    hosts: str |  None
#
# class ListRequest(BaseModel):
#     hosts: Optional[str] = None
#     proxmox_node: Optional[str] = "px-testing"
#     # vault_password_file: Optional[str] = None  # fallback CLI optionnel


class ListRequest(BaseModel):
    # hosts: Optional[str] = None
    # proxmox_node: Optional[str] = "px-testing"
    # as_json: Optional[bool] = True

    # hosts: str | None = None

    proxmox_node: str | None = "px-testing"
    as_json: bool | None = True
