"""Provider-independent workshop inputs and deterministic native resource plans.

This is an explicit native scenario capability, not a platform-stack generator.
The installation context owns credentials; deployments own resource claims.
Identity-provider subjects and network device grants are separate bindings.
"""
from __future__ import annotations

from datetime import datetime, timezone
from ipaddress import IPv4Network
import re
import secrets
import subprocess
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.errors import Range42Error


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class WorkshopDescriptor(StrictModel):
    version: Literal[1]
    kind: Literal['native-installation']
    title: str = Field(min_length=1, max_length=120)
    min_seats: int = Field(default=1, ge=1, le=10)
    max_seats: int = Field(default=10, ge=1, le=10)

    @model_validator(mode='after')
    def bounds(self):
        if self.min_seats > self.max_seats:
            raise ValueError('Workshop seat bounds are inconsistent')
        return self


class WorkshopSpec(StrictModel):
    namespace: str = Field(pattern=r'^[a-z][a-z0-9-]{0,19}$')
    seats: int = Field(ge=1, le=10)
    expires_at: str = Field(max_length=40)
    vmid_start: int = Field(ge=1000, le=999999980)
    network_pool: str = Field(max_length=18)
    bridge_start: int = Field(ge=1, le=999999990)
    proxmox_template: int = Field(ge=100, le=999999999)
    deployer_template: int = Field(ge=100, le=999999999)
    storage: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_.-]{0,63}$')
    identity_profile: str | None = Field(default=None, pattern=r'^[a-z][a-z0-9-]{0,63}$')
    access_profile: str | None = Field(default=None, pattern=r'^[a-z][a-z0-9-]{0,63}$')

    @model_validator(mode='after')
    def coherent(self):
        expiry = datetime.fromisoformat(self.expires_at.replace('Z', '+00:00'))
        if expiry.tzinfo is None or expiry.utcoffset() is None:
            raise ValueError('Workshop expiry requires an explicit time zone')
        self.expires_at = expiry.astimezone(timezone.utc).isoformat()
        pool = IPv4Network(self.network_pool, strict=True)
        private = [IPv4Network(cidr) for cidr in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')]
        if (not 16 <= pool.prefixlen <= 28 or not any(pool.subnet_of(net) for net in private)
                or 2 ** (28 - pool.prefixlen) < self.seats):
            raise ValueError('Choose a private IPv4 pool containing one /28 per participant')
        targets = range(self.vmid_start, self.vmid_start + 2 * self.seats)
        if self.proxmox_template == self.deployer_template or any(
                template in targets for template in (self.proxmox_template, self.deployer_template)):
            raise ValueError('Templates must be distinct and outside the participant VMID allocation')
        return self


class ParticipantBinding(StrictModel):
    username: str = Field(pattern=r'^user(?:0[1-9]|10)$')
    issuer: str = Field(min_length=1, max_length=512)
    subject: str = Field(min_length=1, max_length=256)

    @model_validator(mode='after')
    def identity(self):
        url = urlsplit(self.issuer)
        if (url.scheme != 'https' or not url.hostname or url.username or url.password
                or url.fragment or url.query or any(ord(c) < 32 for c in self.issuer + self.subject)):
            raise ValueError('Use the verified issuer and opaque subject, never an inferred email identity')
        return self

    @property
    def principal(self):
        return self.issuer, self.subject


def compile_plan(spec: WorkshopSpec, *, run_id: str) -> dict:
    if not re.fullmatch(r'[a-f0-9]{16,32}', run_id):
        raise ValueError('Workshop resource ownership requires an immutable deployment ID')
    subnets = IPv4Network(spec.network_pool).subnets(new_prefix=28)
    participants, guests, assignments, networks = [], [], [], []
    for seat in range(1, spec.seats + 1):
        network, username = next(subnets), f'user{seat:02}'
        bridge = f'vmbr{spec.bridge_start + seat - 1}'
        networks.append({'vnet': bridge, 'subnet': str(network), 'gateway': str(network[1]),
                         'mode': 'existing_bridge', 'seat': seat})
        participants.append({'username': username, 'seat': seat, 'principal_key': f'{run_id}:{username}',
                             'proxmox_address': str(network[2]), 'deployer_address': str(network[3])})
        for offset, role, suffix, template, cores, memory, disk in (
                (0, 'proxmox', 'pve', spec.proxmox_template, 4, 16384, 160),
                (1, 'deployer', 'cli', spec.deployer_template, 2, 4096, 32)):
            guest = {'vm_id': spec.vmid_start + 2 * (seat - 1) + offset,
                     'vm_name': f'{spec.namespace}-{username}-{suffix}',
                     'seat': seat, 'username': username, 'role': role,
                     'template': template, 'cores': cores, 'memory_mib': memory, 'disk_gib': disk,
                     'storage': spec.storage,
                     'bridge': bridge, 'ip': str(network[2 + offset]), 'prefix': 28,
                     'gateway': str(network[1]), 'alias': f'r42.{spec.namespace}.{username}.{role}',
                     'owner': f'range42-workshop:{run_id}:{username}:{role}'}
            guests.append(guest)
            assignments.append({'vm_id': guest['vm_id'], 'vm_name': guest['vm_name'],
                                'run_namespace': spec.namespace,
                                'exclusive_network': str(network),
                                'nics': [{'index': 0, 'bridge': bridge, 'ip': guest['ip']}]})
    return {'version': 1, 'run_id': run_id, **spec.model_dump(),
            'participants': participants, 'guests': guests, 'assignments': assignments,
            'networks': networks,
            'capacity': {'memory_mib': spec.seats * 20480, 'vcpus': spec.seats * 6,
                         'disk_gib': spec.seats * 192}}


def validate_activation(plan: dict, *, now: datetime | None = None):
    if datetime.fromisoformat(plan['expires_at']) <= (now or datetime.now(timezone.utc)):
        raise Range42Error(status=409, code='WORKSHOP_EXPIRED', message='This workshop has expired; retire its resources or create a new run.')


def create_credentials(plan: dict) -> dict:
    material = {}
    for person in plan['participants']:
        record = {}
        for role in ('proxmox', 'deployer'):
            password = secrets.token_urlsafe(18)
            try:
                result = subprocess.run(['openssl', 'passwd', '-6', '-stdin'], input=password,
                    text=True, capture_output=True, check=True, timeout=5)
                encoded = result.stdout.strip()
                if not re.fullmatch(r'\$6\$[A-Za-z0-9./]{1,16}\$[A-Za-z0-9./]{86}', encoded):
                    raise ValueError()
            except (OSError, subprocess.SubprocessError, ValueError):
                raise Range42Error(status=503, code='WORKSHOP_CREDENTIAL_RUNTIME_UNAVAILABLE',
                    message='Secure workshop credential generation is unavailable on this backend.') from None
            record[f'{role}_password'] = password
            record[f'{role}_password_hash'] = encoded
        material[person['username']] = record
    return material


def resolve_native_workshop(native: dict, descriptor: dict, *, run_id: str) -> dict | None:
    definition = descriptor.get('workshop')
    request = native.get('workshop')
    if not definition and request is None:
        return None
    if not definition or request is None:
        raise Range42Error(status=409, code='WORKSHOP_SPEC_REQUIRED', message='Choose the workshop setup for a scenario that declares manifest/workshop.json.')
    if any(key.upper().startswith(('WORKSHOP_', 'RANGE42_WORKSHOP_')) for key in native.get('parameters', {})):
        raise Range42Error(status=422, code='WORKSHOP_MANAGED_PARAMETER', message='Workshop resources and participants are managed by the saved run setup.')
    spec = WorkshopSpec.model_validate(request)
    if not definition['min_seats'] <= spec.seats <= definition['max_seats']:
        raise Range42Error(status=422, code='WORKSHOP_SEAT_LIMIT', message='The selected participant count is outside this workshop template’s limits.')
    if descriptor['declared_vmids']:
        raise Range42Error(status=409, code='WORKSHOP_STATIC_RESOURCES', message='Reusable workshop scenarios must declare their resources through the run plan, without fixed guest VMIDs.')
    if spec.identity_profile or spec.access_profile:
        # An arbitrary browser-supplied provider name never authorizes a root
        # integration. Installation adapters must register their capabilities.
        raise Range42Error(status=409, code='WORKSHOP_PROVIDER_UNAVAILABLE', message='The selected identity or access integration is not registered for independent workshop runs.')
    plan = compile_plan(spec, run_id=run_id)
    if previous := native.get('workshop_plan'):
        if previous != plan:
            raise Range42Error(status=409, code='WORKSHOP_PLAN_CHANGED', message='The saved workshop resource plan changed. Restore its recorded definition before operating on this run.')
    return plan
