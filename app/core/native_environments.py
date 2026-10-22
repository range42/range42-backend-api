"""Register bounded provisioning profiles on trusted, existing native runtimes.

Registration enables planning. Infrastructure, isolation, source-template and
capacity qualification remain separate live preflight requirements.
"""
import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator
from sqlalchemy import select

from app.core.errors import Range42Error
from app.core.models import ProxmoxHost
from app.core.native_environment_models import NativeEnvironment
from app.core.native_environment_infrastructure import GatewayFoundation, gateway_capacity, gateway_outside_run
from app.core.native_contexts import NativeContext, _api_binding, _contexts, resolve_context
from app.core.workshop_profiles import WorkshopProfile
from app.core.provisioning_profiles import ProvisioningProfile


class RuntimeTarget(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    version: Literal[1]
    physical_node_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    hardware_binding: str = Field(pattern=r'^[a-f0-9]{64}$')
    node_name: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9.-]{0,127}$')
    installation_id: str = Field(pattern=r'^[a-f0-9]{32}$')


class EnvironmentSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    label: str = Field(min_length=1, max_length=128, pattern=r'^[^\r\n\x00-\x1f]+$')
    base_context_id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')
    target_host_id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')
    workshop_profile: WorkshopProfile | None = None
    provisioning_profile: ProvisioningProfile | None = None
    gateway_foundation: GatewayFoundation | None = None
    reuse_runtime: bool = Field(default=False, strict=True)

    @model_serializer(mode='wrap')
    def compatible_selection(self, handler):
        value = handler(self)
        if self.workshop_profile is None:
            value.pop('workshop_profile',None)
        if self.provisioning_profile is None:
            value.pop('provisioning_profile',None)
        if not self.reuse_runtime:
            value.pop('reuse_runtime', None)
        return value

    @property
    def resource_profile(self):
        return self.provisioning_profile or self.workshop_profile

    @model_validator(mode='after')
    def retained_gateway_scope(self):
        if (self.workshop_profile is None)==(self.provisioning_profile is None):
            raise ValueError('Select exactly one provisioning profile')
        if self.gateway_foundation and self.resource_profile.managed_gateway:
            raise ValueError('Choose one managed gateway per run or a retained shared gateway')
        if self.gateway_foundation and not gateway_outside_run(self.gateway_foundation.vmid, self.resource_profile):
            raise ValueError('The retained gateway must be separate from participant guests and source templates')
        return self


def refused(message):
    return Range42Error(status=409, code='NATIVE_ENVIRONMENT_REVIEW_REQUIRED', message=message)


def selection_for(row):
    return EnvironmentSelection(label=row.label, base_context_id=row.base_context_id,
        target_host_id=row.target_host_id, **{('provisioning_profile' if 'templates' in json.loads(row.profile_json) else 'workshop_profile'):json.loads(row.profile_json)},
        gateway_foundation=json.loads(row.foundation_json) if row.foundation_json else None,
        reuse_runtime=row.runtime_target_json is not None)


async def runtime_target_proof(session, selection, host):
    """Read a retained exact target proof; never enroll or contact a new node."""
    if not selection.reuse_runtime:
        return None
    from app.core.physical_node_models import PhysicalNodeBinding
    from app.core.physical_nodes import NodeAttestation, registry_revision, target_revision
    from app.core.preparation_transport import agent_configuration
    try:
        if host is None:
            raise ValueError()
        await registry_revision(session)
        binding = await session.get(PhysicalNodeBinding, host.id, populate_existing=True)
        configuration = await asyncio.to_thread(agent_configuration, host.id, host.node_name)
        if (binding is None or binding.target_revision != target_revision(host)
                or binding.agent_revision != configuration.revision):
            raise ValueError()
        observed = NodeAttestation.model_validate_json(binding.observation_json)
        if observed.node_name != host.node_name or observed.installation_id != configuration.installation_id:
            raise ValueError()
        identity = RuntimeTarget(version=1, **{field: getattr(observed, field)
            for field in ('physical_node_id', 'hardware_binding', 'node_name', 'installation_id')})
        return {'identity': identity.model_dump(), 'binding_revision': binding.observation_digest,
                'agent_revision': configuration.revision}
    except (Range42Error, ValueError, AttributeError):
        raise refused('Inspect and record the selected target’s physical node with its current credential and enrolled service configuration before reusing a runtime.') from None


def require_original_runtime_target(row, proof):
    if row.runtime_target_json is not None:
        try:
            original = RuntimeTarget.model_validate_json(row.runtime_target_json)
            if proof is None or original.model_dump() != proof['identity']:
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise refused('The reviewed runtime target identity changed. Preserve its original scope and reconcile the physical node before revalidation.') from None


def review_selection(selection, host, base, *, target_proof=None):
    if host is None or base is None:
        raise refused('Choose an existing registered target and a trusted native runtime.')
    try:
        if selection.reuse_runtime:
            _api_binding(base.target_api_url)
            _api_binding(host.api_url)
            binding_matches = bool(base.target_node and target_proof
                and target_proof['identity']['node_name'] == host.node_name)
        else:
            binding_matches = bool(base.target_api_url and base.target_node
                and base.address == urlsplit(host.api_url).hostname and base.target_node == host.node_name
                and _api_binding(base.target_api_url) == _api_binding(host.api_url))
    except ValueError:
        binding_matches = False
    if not binding_matches:
        raise refused('The selected target must use the trusted runtime’s exact node and HTTPS API endpoint.')
    if base.issues():
        raise refused('Prepare the trusted native runtime before registering this environment. ' + ' '.join(base.issues()))
    try:
        identity, secret = host.token_ref.split('=', 1)
        user, token = identity.split('!', 1)
        if not all((user, token, secret)) or any(char.isspace() for char in host.token_ref):
            raise ValueError()
    except (AttributeError, ValueError):
        raise refused('Register the dedicated target’s Proxmox API credential first.') from None
    files = [base.context_script, base.workspace / 'sourced_range42.sh',
             base.workspace / 'inventory/inventory_default.yml', base.workspace / 'secrets/default_vault.yml',
             base.workspace / 'secrets/vault_pass.txt']
    try:
        hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in files]
    except OSError:
        raise refused('The trusted runtime changed during review. Refresh and review it again.') from None
    intent = {'selection': selection.model_dump(exclude={'gateway_foundation'} if selection.gateway_foundation is None else set()), 'node': host.node_name, 'api': host.api_url,
        'credential': hashlib.sha256(host.token_ref.encode()).hexdigest(),
        'target_defaults': {'bridge': host.default_bridge, 'protected_vmids': host.protected_vmids_override_json},
        'runtime_files': hashes, 'runtime': {'workspace': str(base.workspace), 'script': str(base.context_script),
            'codename': base.codename, 'scenario': base.scenario, 'variables': base.inventory_variables,
            'binding': [base.target_host_id, base.target_node, base.target_api_url]}}
    if selection.reuse_runtime:
        intent['runtime_target_proof'] = target_proof
    revision = hashlib.sha256(json.dumps(intent, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'revision': revision, 'label': selection.label, 'base_context_id': base.id,
        'target_host_id': host.id, **{('provisioning_profile' if selection.provisioning_profile else 'workshop_profile'): selection.resource_profile.public()},
        'runtime_target': target_proof['identity'] if target_proof else None,
        'gateway_foundation': selection.gateway_foundation.model_dump() if selection.gateway_foundation else None,
        'gateway_nic_capacity': gateway_capacity(selection.gateway_foundation, selection.resource_profile.networks) if selection.gateway_foundation else None,
        'ready_for_planning': True, 'infrastructure_qualified': False,
        'requirements': ['Live preflight must qualify source templates, isolated bridges, guest ownership and available capacity.',
            'Laptop access, routing, DNS and browser trust require the selected installation’s access integration.']}


async def review_inputs(session, selection):
    host = await session.get(ProxmoxHost, selection.target_host_id)
    bases = await asyncio.to_thread(_contexts)
    base = next((item for item in bases if item.id == selection.base_context_id), None)
    proof = await runtime_target_proof(session, selection, host)
    return await asyncio.to_thread(review_selection, selection, host, base, target_proof=proof), host


async def review(session, selection):
    result, host = await review_inputs(session, selection)
    if selection.gateway_foundation:
        from app.core.native_environment_infrastructure import observe_foundation
        if selection.resource_profile.infrastructure_guests:
            raise refused('Choose one infrastructure binding mode for this environment.')
        observed = await observe_foundation(host, selection.gateway_foundation.vmid, selection.resource_profile.networks)
        if observed['gateway_foundation'] != selection.gateway_foundation:
            raise refused('The gateway changed after inspection. Inspect its current generation and ports again.')
    if selection.resource_profile.infrastructure_guests:
        from app.core.native_environment_infrastructure import canonical, observe, refused as infrastructure_refused
        expected = selection.resource_profile.infrastructure_guests
        actual = await observe(host, [guest.vmid for guest in expected],
            {network.bridge for network in selection.resource_profile.networks})
        if canonical(expected) != canonical(actual):
            raise infrastructure_refused()
    return result


async def registered_contexts(session, *, identity=None):
    query = select(NativeEnvironment).order_by(NativeEnvironment.created_at, NativeEnvironment.id)
    if identity is not None:
        query = query.where(NativeEnvironment.id == identity)
    rows = list(await session.scalars(query))
    if not rows:
        return []
    bases = {item.id: item for item in await asyncio.to_thread(_contexts)}
    result = []
    for row in rows:
        host = await session.get(ProxmoxHost, row.target_host_id)
        base = bases.get(row.base_context_id)
        issue = ''
        try:
            selection = selection_for(row)
            proof = await runtime_target_proof(session, selection, host)
            require_original_runtime_target(row, proof)
            current = await asyncio.to_thread(review_selection, selection, host, base, target_proof=proof)
            if (current['revision'] != row.review_revision or host.node_name != row.target_node
                    or _api_binding(host.api_url) != _api_binding(row.target_api_url)):
                raise refused('Environment inputs or credentials changed. An administrator must review and revalidate this environment.')
        except Range42Error as error:
            issue = error.message
        if base is None:
            base = NativeContext(row.base_context_id, row.label, Path('/__unavailable_native_runtime__'),
                Path('/__unavailable_native_runtime__'), '', '', urlsplit(row.target_api_url).hostname or '')
        selection=selection_for(row)
        result.append(replace(base, id=row.id, label=row.label,
            target_host_id=row.target_host_id, target_node=row.target_node, target_api_url=row.target_api_url,
            address=urlsplit(row.target_api_url).hostname or '', node_name=row.target_node,
            workshop_profile=selection.workshop_profile, provisioning_profile=selection.provisioning_profile, registration_issue=issue,
            purpose='native' if selection.provisioning_profile else 'workshop', execution_ready=False))
    return result


async def resolve_registered_context(session, identity, host):
    # Existing native contexts keep their file-registry contract. Looking up a
    # generated environment must not make every legacy attempt depend on all
    # stored provisioning profiles or repeatedly hash unrelated runtime files.
    if not re.fullmatch(r'env-[a-f0-9]{24}', identity):
        return await asyncio.to_thread(resolve_context, identity, host)
    contexts = await registered_contexts(session, identity=identity)
    return await asyncio.to_thread(resolve_context, identity, host, registered=contexts)
