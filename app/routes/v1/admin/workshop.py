"""Admin-only workshop control through an optional local privileged worker.

The API never receives identity-provider credentials. A bounded Unix-socket
protocol separates the service from the workshop operator's private state.
"""
import asyncio
import base64
import json
import os
from pathlib import Path
import socket
import stat
import struct
from typing import Literal

from fastapi import APIRouter, Path as PathParameter
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.errors import Range42Error

router = APIRouter(prefix='/workshop', tags=['v1 workshop administration'])
USERNAME = r'^user(?:0[1-9]|10)$'


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
            raw = await asyncio.wait_for(reader.readline(), 30)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError()
            result = json.loads(raw)
            if not result.get('ok'):
                raise ValueError()
            return result['result']
        finally:
            writer.close()
            await writer.wait_closed()
    except (OSError, ValueError, KeyError, asyncio.TimeoutError, RuntimeError):
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
            'workshop-controller', stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            raw, _ = await asyncio.wait_for(process.communicate(json.dumps(value).encode() + b'\n'), 30)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise
        if process.returncode or len(raw) > 2 * 1024 * 1024:
            raise ValueError()
        result = json.loads(raw)
        if not result.get('ok'):
            raise ValueError()
        return result['result']
    except (OSError, ValueError, KeyError, asyncio.TimeoutError):
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


class WorkshopStatus(BaseModel):
    id: str
    expires_at: str
    seats: list[Seat]
    operations: list[Operation] = Field(default_factory=list)
    configuration_revision: str = ''


class WorkshopAction(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    action: Literal['prepare', 'accounts', 'keys', 'handouts', 'bind', 'revoke', 'close',
                    'deploy', 'configure', 'reset', 'teardown']
    username: str | None = Field(default=None, pattern=USERNAME)
    node_id: int | None = Field(default=None, gt=0)
    seats: int | None = Field(default=None, ge=1, le=10)
    confirmation: str | None = None

    @model_validator(mode='after')
    def validate_scope(self):
        seat_actions = {'bind', 'revoke', 'close', 'deploy', 'configure', 'reset', 'teardown'}
        if (self.action in seat_actions) != (self.username is not None):
            raise ValueError('Select one participant for a seat action')
        if (self.action == 'bind') != (self.node_id is not None):
            raise ValueError('Select an observed device only when binding')
        if (self.action == 'prepare') != (self.seats is not None):
            raise ValueError('Select the seat count only when preparing a workshop')
        if self.action in {'reset', 'teardown', 'close'}:
            if self.confirmation != self.username:
                raise ValueError('Confirm the selected participant before resetting, deleting or closing it')
        elif self.confirmation is not None:
            raise ValueError('Unexpected confirmation')
        return self


@router.get('', response_model=WorkshopStatus)
async def status():
    return await agent_request({'action': 'status'})


@router.post('/actions', response_model=Operation, status_code=202)
async def action(request: WorkshopAction):
    return await agent_request(request.model_dump(exclude={'confirmation'}))


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
