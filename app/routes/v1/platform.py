"""Build reviewable platform components using the operator-installed release."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.errors import Range42Error

router = APIRouter(prefix='/platform', tags=['v1 platform'])


class OperatorNetworkPeer(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    vm_id: int = Field(ge=100, le=999999999)
    vm_name: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$')
    description: str = Field(min_length=1, max_length=256)

    @field_validator('description')
    @classmethod
    def explicit_ownership(cls, value):
        if not value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError('Operator peer requires an exact nonempty ownership description')
        return value


class StackSpec(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    id: str = Field(pattern=r'^[a-z][a-z0-9-]{0,23}$')
    domain: str = Field(min_length=3, max_length=220)
    vmid_start: int = Field(ge=1000, le=999999988)
    template_vmid: int = Field(ge=102, le=999999999)
    subnet: str = Field(max_length=32)
    gateway: str = Field(max_length=15)
    bridge: str = Field(pattern=r'^(?:[a-z][a-z0-9]{1,7}|vmbr[1-9][0-9]{0,8})$')
    node: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$')
    ssh_user: str = Field(pattern=r'^[a-z_][a-z0-9_-]{0,31}$')
    dns: str = Field(default='1.1.1.1', max_length=15)
    profile: Literal['core', 'full'] = 'core'
    network_mode: Literal['sdn', 'existing_bridge'] = 'sdn'
    network_peers: list[OperatorNetworkPeer] = Field(default_factory=list, max_length=32)

    @model_validator(mode='after')
    def network_binding(self):
        if self.network_mode == 'existing_bridge':
            if not re.fullmatch(r'vmbr[1-9][0-9]{0,8}', self.bridge):
                raise ValueError('Choose a dedicated existing Linux bridge other than vmbr0')
        elif (not re.fullmatch(r'[a-z][a-z0-9]{1,7}', self.bridge)
              or re.fullmatch(r'vmbr\d+', self.bridge)
              or 'network_peers' in self.model_fields_set):
            raise ValueError('SDN mode needs a new VNet name and cannot bind operator peers')
        return self

    def generator_json(self):
        # Preserve the input contract of installed releases predating bridge mode.
        exclude = {'network_mode', 'network_peers'} if self.network_mode == 'sdn' else set()
        return self.model_dump_json(exclude=exclude)


@router.post('/components/preview')
def preview_component(spec: StackSpec):
    source = os.getenv('RANGE42_PLATFORM_PLAYBOOKS_DIR')
    if not source or not (Path(source) / 'range42_stack/project.py').is_file():
        raise Range42Error(status=503, code='PLATFORM_RUNTIME_UNAVAILABLE',
                           message='Install the Range42 platform component runtime on this backend.')
    try:
        result = subprocess.run([sys.executable, '-m', 'range42_stack.project'],
            cwd=source, env={**os.environ, 'PYTHONPATH': source, 'PYTHONDONTWRITEBYTECODE': '1'},
            input=spec.generator_json(), capture_output=True, text=True, timeout=20, check=False)
        if len(result.stdout) > 4 * 1024 * 1024:
            raise ValueError('Platform component exceeds the project file limit')
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            raise ValueError('Installed platform generator returned an invalid component')
        if result.returncode:
            raise ValueError(value.get('error', 'Platform component could not be generated'))
        if value.get('version') != 1 or not isinstance(value.get('files'), dict):
            raise ValueError('Installed platform generator returned an invalid component')
        return value
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        raise Range42Error(status=422, code='PLATFORM_COMPONENT_INVALID', message=str(error)) from None
