"""Whitelisted SDN control-plane inventory for topology planning."""
from typing import Annotated, Generic, Literal, TypeVar
from pydantic import BaseModel, Field

SdnName = Annotated[str, Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')]
SdnView = Literal['running', 'pending']


class SdnState(BaseModel):
    state: str | None = None
    has_pending: bool = False


class SdnZone(SdnState):
    zone: SdnName
    type: Annotated[str, Field(min_length=1, max_length=64)]
    nodes: list[SdnName] = Field(default_factory=list)


class SdnVnet(SdnState):
    vnet: SdnName
    zone: SdnName


class SdnSubnet(SdnState):
    subnet: Annotated[str, Field(min_length=1, max_length=256)]
    vnet: SdnName
    cidr: str
    gateway: str | None = None
    snat: bool = False


T = TypeVar('T')


class SdnPage(BaseModel, Generic[T]):
    items: list[T]
    total: int
    offset: int
    limit: int
    view: SdnView
    visibility: Literal['credential_filtered'] = 'credential_filtered'
