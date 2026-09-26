"""Build reviewable platform components using the operator-installed release."""
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import Range42Error

router = APIRouter(prefix='/platform', tags=['v1 platform'])


class StackSpec(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    id: str = Field(pattern=r'^[a-z][a-z0-9-]{0,23}$')
    domain: str = Field(min_length=3, max_length=220)
    vmid_start: int = Field(ge=1000, le=999999988)
    template_vmid: int = Field(ge=102, le=999999999)
    subnet: str = Field(max_length=32)
    gateway: str = Field(max_length=15)
    bridge: str = Field(pattern=r'^[a-z][a-z0-9]{1,7}$')
    node: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$')
    ssh_user: str = Field(pattern=r'^[a-z_][a-z0-9_-]{0,31}$')
    dns: str = Field(default='1.1.1.1', max_length=15)
    profile: Literal['core', 'full'] = 'core'


@router.post('/components/preview')
def preview_component(spec: StackSpec):
    source = os.getenv('RANGE42_PLATFORM_PLAYBOOKS_DIR')
    if not source or not (Path(source) / 'range42_stack/project.py').is_file():
        raise Range42Error(status=503, code='PLATFORM_RUNTIME_UNAVAILABLE',
                           message='Install the Range42 platform component runtime on this backend.')
    try:
        result = subprocess.run([sys.executable, '-m', 'range42_stack.project'],
            cwd=source, env={**os.environ, 'PYTHONPATH': source, 'PYTHONDONTWRITEBYTECODE': '1'},
            input=spec.model_dump_json(), capture_output=True, text=True, timeout=20, check=False)
        if len(result.stdout) > 4 * 1024 * 1024:
            raise ValueError('Platform component exceeds the project file limit')
        value = json.loads(result.stdout)
        if result.returncode:
            raise ValueError(value.get('error', 'Platform component could not be generated'))
        if value.get('version') != 1 or not isinstance(value.get('files'), dict):
            raise ValueError('Installed platform generator returned an invalid component')
        return value
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        raise Range42Error(status=422, code='PLATFORM_COMPONENT_INVALID', message=str(error)) from None
