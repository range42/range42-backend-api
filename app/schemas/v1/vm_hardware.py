"""Narrow existing-QEMU hardware changes; no create/delete or arbitrary PVE body."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class NicChanges(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    bridge: str | None = Field(default=None, pattern=r'^[A-Za-z][A-Za-z0-9_.-]{0,14}$')
    tag: int | None = Field(default=None, ge=1, le=4094)
    firewall: bool | None = None
    link_down: bool | None = None

    @model_validator(mode='after')
    def nonempty(self):
        if not self.model_fields_set or any(getattr(self, key) is None for key in self.model_fields_set - {'tag'}):
            raise ValueError('Choose supported NIC changes; only the VLAN tag can be cleared')
        return self


class NicUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    changes: NicChanges


class DiskGrowth(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    size_gb: int = Field(ge=1, le=65536)


class NicView(BaseModel):
    id: str
    model: str | None = None
    mac: str | None = None
    bridge: str | None = None
    tag: int | None = None
    firewall: bool = False
    link_down: bool = False
    editable: bool = False


class DiskView(BaseModel):
    id: str
    size_bytes: int | None = None
    pool: str | None = None
    volume_fingerprint: str | None = None
    editable: bool = False


class HardwareValues(BaseModel):
    nics: list[NicView]
    disks: list[DiskView]


class HardwareReview(BaseModel):
    host_id: str
    node: str
    vmid: int
    vmtype: Literal['qemu'] = 'qemu'
    digest: str
    target_digest: str
    current: HardwareValues
    configured: HardwareValues
    pending: list[str]


class HardwareResult(BaseModel):
    status: Literal['configured', 'accepted', 'unconfirmed']
    upid: str | None = None
    review: HardwareReview | None = None
    reason: Literal['write_outcome_unknown', 'unexpected_write_response', 'readback_unavailable', 'readback_mismatch'] | None = None
