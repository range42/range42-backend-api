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

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from app.core.errors import Range42Error
from app.core.models import ProxmoxHost
from app.core.native_environment_models import NativeEnvironment
from app.core.native_environment_infrastructure import GatewayFoundation, gateway_capacity, gateway_outside_run
from app.core.native_contexts import NativeContext, _api_binding, _contexts, resolve_context
from app.core.workshop_profiles import WorkshopProfile


class EnvironmentSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    label: str = Field(min_length=1, max_length=128, pattern=r'^[^\r\n\x00-\x1f]+$')
    base_context_id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')
    target_host_id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')
    workshop_profile: WorkshopProfile
    gateway_foundation: GatewayFoundation | None = None

    @model_validator(mode='after')
    def retained_gateway_scope(self):
        if self.gateway_foundation and not gateway_outside_run(self.gateway_foundation.vmid, self.workshop_profile):
            raise ValueError('The retained gateway must be separate from participant guests and source templates')
        return self


def refused(message):
    return Range42Error(status=409, code='NATIVE_ENVIRONMENT_REVIEW_REQUIRED', message=message)


def selection_for(row):
    return EnvironmentSelection(label=row.label, base_context_id=row.base_context_id,
        target_host_id=row.target_host_id, workshop_profile=json.loads(row.profile_json),
        gateway_foundation=json.loads(row.foundation_json) if row.foundation_json else None)


def review_selection(selection, host, base):
    if host is None or base is None:
        raise refused('Choose an existing registered target and a trusted native runtime.')
    try:
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
    revision = hashlib.sha256(json.dumps(intent, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'revision': revision, 'label': selection.label, 'base_context_id': base.id,
        'target_host_id': host.id, 'workshop_profile': selection.workshop_profile.public(),
        'gateway_foundation': selection.gateway_foundation.model_dump() if selection.gateway_foundation else None,
        'gateway_nic_capacity': gateway_capacity(selection.gateway_foundation, selection.workshop_profile.networks) if selection.gateway_foundation else None,
        'ready_for_planning': True, 'infrastructure_qualified': False,
        'requirements': ['Live preflight must qualify source templates, isolated bridges, guest ownership and available capacity.',
            'Laptop access, routing, DNS and browser trust require the selected installation’s access integration.']}


async def review_inputs(session, selection):
    host = await session.get(ProxmoxHost, selection.target_host_id)
    bases = await asyncio.to_thread(_contexts)
    base = next((item for item in bases if item.id == selection.base_context_id), None)
    return await asyncio.to_thread(review_selection, selection, host, base), host


async def review(session, selection):
    result, host = await review_inputs(session, selection)
    if selection.gateway_foundation:
        from app.core.native_environment_infrastructure import observe_foundation
        if selection.workshop_profile.infrastructure_guests:
            raise refused('Choose one infrastructure binding mode for this environment.')
        observed = await observe_foundation(host, selection.gateway_foundation.vmid, selection.workshop_profile.networks)
        if observed['gateway_foundation'] != selection.gateway_foundation:
            raise refused('The gateway changed after inspection. Inspect its current generation and ports again.')
    if selection.workshop_profile.infrastructure_guests:
        from app.core.native_environment_infrastructure import canonical, observe, refused as infrastructure_refused
        expected = selection.workshop_profile.infrastructure_guests
        actual = await observe(host, [guest.vmid for guest in expected],
            {network.bridge for network in selection.workshop_profile.networks})
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
            current = await asyncio.to_thread(review_selection, selection, host, base)
            if (current['revision'] != row.review_revision or host.node_name != row.target_node
                    or _api_binding(host.api_url) != _api_binding(row.target_api_url)):
                raise refused('Environment inputs or credentials changed. An administrator must review and revalidate this environment.')
        except Range42Error as error:
            issue = error.message
        if base is None:
            base = NativeContext(row.base_context_id, row.label, Path('/__unavailable_native_runtime__'),
                Path('/__unavailable_native_runtime__'), '', '', urlsplit(row.target_api_url).hostname or '')
        result.append(replace(base, id=row.id, label=row.label,
            target_host_id=row.target_host_id, target_node=row.target_node, target_api_url=row.target_api_url,
            workshop_profile=WorkshopProfile.model_validate_json(row.profile_json), registration_issue=issue,
            purpose='workshop', execution_ready=False))
    return result


async def resolve_registered_context(session, identity, host):
    # Existing native contexts keep their file-registry contract. Looking up a
    # generated environment must not make every legacy attempt depend on all
    # stored provisioning profiles or repeatedly hash unrelated runtime files.
    if not re.fullmatch(r'env-[a-f0-9]{24}', identity):
        return await asyncio.to_thread(resolve_context, identity, host)
    contexts = await registered_contexts(session, identity=identity)
    return await asyncio.to_thread(resolve_context, identity, host, registered=contexts)
