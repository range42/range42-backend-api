"""Admin-only workshop control through an optional local privileged worker.

The API never receives identity-provider credentials. A bounded Unix-socket
protocol separates the service from the workshop operator's private state.
"""
import asyncio
import base64
import json
import os
import socket
import stat
import struct
from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from fastapi import Path as PathParameter
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.errors import Range42Error

router = APIRouter(prefix='/workshop', tags=['v1 workshop administration'])
USERNAME = r'^user(?:0[1-9]|10)$'


def agent_result(value):
    if not isinstance(value, dict) or type(value.get('ok')) is not bool:
        raise ValueError()
    if value['ok'] is True and isinstance(value.get('result'), dict):
        return value['result']
    messages = {
        'WORKSHOP_PAUSED': (423, 'Workshop controller operations are paused. Refresh admission before making changes.'),
        'WORKSHOP_ADMISSION_CHANGED': (409, 'Admission changed. Refresh and review the current pause before retrying.'),
        'WORKSHOP_DRAINING': (409, 'Accepted workshop operations are still draining. Wait for them to finish before resuming.'),
    }
    if value.get('code') in messages:
        status, message = messages[value['code']]
        raise Range42Error(status=status, code=value['code'], message=message)
    raise ValueError()


def unavailable():
    return Range42Error(status=503, error='workshop_unavailable', code='WORKSHOP_UNAVAILABLE',
                        message='The workshop management worker is unavailable. Contact the platform administrator.')


async def agent_request(value):
    ssh_config = os.environ.get('RANGE42_WORKSHOP_SSH_CONFIG')
    if ssh_config:
        return await ssh_agent_request(ssh_config, value)
    path = Path(os.environ.get('RANGE42_WORKSHOP_SOCKET', ''))
    try:
        if not path.is_absolute() or path.name != 'control.sock':
            raise ValueError()
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o007:
            raise ValueError()
        reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(path), limit=2 * 1024 * 1024), 5)
        try:
            peer = writer.get_extra_info('socket').getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            if struct.unpack('3i', peer)[1] != 0:
                raise ValueError()
            writer.write(json.dumps(value).encode() + b'\n')
            await writer.drain()
            timeout = 120 if value.get('action') in {'ownership', 'ownership-begin', 'ownership-commit'} else 30
            raw = await asyncio.wait_for(reader.readline(), timeout)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError()
            return agent_result(json.loads(raw))
        finally:
            writer.close()
            await writer.wait_closed()
    except (TimeoutError, OSError, ValueError, KeyError, RuntimeError):
        raise unavailable() from None


async def ssh_agent_request(config, value):
    """The configured key is restricted to the native workshop dispatcher."""
    try:
        path = Path(config)
        info = path.lstat()
        if (not path.is_absolute() or not stat.S_ISREG(info.st_mode)
                or info.st_mode & 0o022 or info.st_uid not in {0, os.getuid()}):
            raise ValueError()
        process = await asyncio.create_subprocess_exec(
            'ssh', '-F', str(path), '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
            '-o', 'StrictHostKeyChecking=yes', 'workshop-controller', stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            timeout = 120 if value.get('action') in {'ownership', 'ownership-begin', 'ownership-commit'} else 30
            raw, _ = await asyncio.wait_for(process.communicate(json.dumps(value).encode() + b'\n'), timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        if process.returncode not in {0, 1} or len(raw) > 2 * 1024 * 1024:
            raise ValueError()
        return agent_result(json.loads(raw))
    except (TimeoutError, OSError, ValueError, KeyError):
        raise unavailable() from None


class Device(BaseModel):
    id: int
    address: str
    name: str = ''
    online: bool
    bound: bool = False


class Seat(BaseModel):
    username: str
    proxmox_address: str
    deployer_address: str
    account_ready: bool = False
    key_ready: bool = False
    handout_ready: bool = False
    closed: bool = False
    device: Device | None = None
    devices: list[Device] = Field(default_factory=list)
    deployment_id: str | None = None
    deployment_state: str = 'not_created'
    certificate_fingerprint: str | None = None


class Operation(BaseModel):
    operation_id: str
    action: str = ''
    username: str | None = None
    state: str
    message: str = ''
    started_at: str | None = None
    finished_at: str | None = None


class WorkshopAdmission(BaseModel):
    scope: Literal['legacy_controller_queue']
    state: Literal['open', 'draining', 'paused']
    revision: int = Field(ge=0, lt=2**53)
    pause_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')
    queued_count: int = Field(ge=0)
    running_count: int = Field(ge=0)


class AdmissionChange(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    action: Literal['pause', 'resume']
    expected_revision: int = Field(ge=0, lt=2**53)
    request_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    pause_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')
    confirmation: str | None = None

    @model_validator(mode='after')
    def reviewed_change(self):
        if self.action == 'resume':
            if self.pause_id is None or self.confirmation != 'RESUME':
                raise ValueError('Review this pause and type RESUME before reopening workshop operations')
        elif self.pause_id is not None or self.confirmation is not None:
            raise ValueError('A pause uses only the current revision and a unique request ID')
        return self


class WorkshopStatus(BaseModel):
    id: str
    expires_at: str
    seats: list[Seat]
    operations: list[Operation] = Field(default_factory=list)
    configuration_revision: str = ''
    cohort_deployment_id: str | None = None
    cohort_seats: int | None = None
    admission: WorkshopAdmission | None = None


class WorkshopAction(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    action: Literal['prepare', 'accounts', 'keys', 'handouts', 'bind', 'revoke', 'close',
                    'deploy', 'configure', 'reset', 'teardown', 'replace_key', 'link']
    deployment_id: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_-]{1,128}$')
    username: str | None = Field(default=None, pattern=USERNAME)
    node_id: int | None = Field(default=None, gt=0)
    seats: int | None = Field(default=None, ge=1, le=10)
    confirmation: str | None = None

    @model_validator(mode='after')
    def validate_scope(self):
        if (self.action == 'link') != (self.deployment_id is not None):
            raise ValueError('Select a saved deployment only when connecting participant management')
        seat_actions = {'bind', 'revoke', 'close', 'deploy', 'configure', 'reset', 'teardown', 'replace_key'}
        if (self.action in seat_actions) != (self.username is not None):
            raise ValueError('Select one participant for a seat action')
        if (self.action == 'bind') != (self.node_id is not None):
            raise ValueError('Select an observed device only when binding')
        if (self.action == 'prepare') != (self.seats is not None):
            raise ValueError('Select the seat count only when preparing a workshop')
        if self.action in {'reset', 'teardown', 'close', 'replace_key'}:
            if self.confirmation != self.username:
                raise ValueError('Confirm the selected participant before resetting, deleting or closing it')
        elif self.confirmation is not None:
            raise ValueError('Unexpected confirmation')
        return self


@router.get('', response_model=WorkshopStatus)
async def status(response: Response):
    response.headers['Cache-Control'] = 'no-store'
    return await agent_request({'action': 'status'})


@router.get('/admission', response_model=WorkshopAdmission)
async def admission(response: Response):
    response.headers['Cache-Control'] = 'no-store'
    return await agent_request({'action': 'admission'})


@router.post('/admission', response_model=WorkshopAdmission)
async def change_admission(request: AdmissionChange, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    return await agent_request(request.model_dump(exclude={'confirmation'}, exclude_none=True))


@router.post('/actions', response_model=Operation, status_code=202)
async def action(request: WorkshopAction):
    return await agent_request(request.model_dump(exclude={'confirmation'} | ({'deployment_id'} if request.deployment_id is None else set())))


@router.get('/seats/{username}/handout')
async def handout(username: str = PathParameter(pattern=USERNAME)):
    result = await agent_request({'action': 'handout', 'username': username})
    try:
        content = base64.b64decode(result['pdf'], validate=True)
        if not content.startswith(b'%PDF-') or len(content) > 1024 * 1024:
            raise ValueError()
    except (KeyError, ValueError):
        raise unavailable() from None
    return Response(content=content, media_type='application/pdf', headers={
        'Cache-Control': 'no-store', 'Pragma': 'no-cache', 'X-Content-Type-Options': 'nosniff',
        'Content-Disposition': f'attachment; filename="{username}.pdf"'})
