"""Deliberately small configuration edit surface for imported guests."""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ConfigField = Literal["name", "description", "cores", "memory", "tags"]


class VmConfigChanges(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str | None = Field(default=None, min_length=1, max_length=63)
    description: str | None = Field(default=None, max_length=8192)
    cores: int | None = Field(default=None, ge=1, le=128)
    memory: int | None = Field(default=None, ge=16, le=4194304, description="MiB")
    tags: str | None = Field(default=None, max_length=1024)

    @field_validator("name")
    @classmethod
    def name_literal(cls, value):
        if value is not None and not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", value):
            raise ValueError("name must be a literal DNS label")
        return value

    @field_validator("description")
    @classmethod
    def description_plain(cls, value):
        if value is not None and ("range42-deployment:" in value.lower() or any(ord(c) < 32 and c not in "\n\t" or ord(c) == 127 for c in value)):
            raise ValueError("description must not contain control characters or deployment ownership markers")
        return value

    @field_validator("tags")
    @classmethod
    def tags_literal(cls, value):
        if value:
            tags = value.split(";")
            if len(set(tags)) != len(tags) or any(not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.+\-]{0,63}", tag) for tag in tags):
                raise ValueError("tags must be unique literal names separated by semicolons")
        return value

    @model_validator(mode="after")
    def nonempty_patch(self):
        if not self.model_fields_set or any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("changes must contain at least one non-null supported field")
        return self


class VmConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    digest: str = Field(pattern=r"^[a-f0-9]{64}$", description="Opaque target-bound digest from config/review")
    changes: VmConfigChanges


class VmConfigValues(BaseModel):
    name: str | None
    description: str | None
    cores: int | None
    memory: int | None
    tags: str | None


class VmConfigReview(BaseModel):
    host_id: str
    node: str
    vmid: int
    vmtype: Literal["qemu", "lxc"]
    digest: str
    target_digest: str = Field(pattern=r"^[a-f0-9]{64}$", description="Stable registered target binding; unchanged by guest configuration edits")
    current: VmConfigValues
    configured: VmConfigValues
    pending: list[ConfigField]


class VmConfigUpdateResult(BaseModel):
    status: Literal["configured", "accepted", "unconfirmed"]
    upid: str | None = None
    review: VmConfigReview | None = None
    reason: Literal["write_outcome_unknown", "unexpected_write_response", "readback_unavailable", "readback_mismatch"] | None = None
