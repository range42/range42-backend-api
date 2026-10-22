"""Optional preparation uses the original Core hold, never a parallel job ledger."""
import asyncio
import json
import uuid
from typing import Literal

from pydantic import Field, field_validator, model_validator

from sqlalchemy import select, text

from app.core import capacity_ledger as ledger, native_capacity
from app.core.capacity_models import CapacityHold
from app.core.models import Deployment, ProxmoxHost
from app.core.physical_node_models import PhysicalNodeBinding
from app.core.physical_nodes import FOUNDATION_OPERATIONS, NodeAttestation, digest, registry_revision, target_revision
from app.core.template_preparation import parse_spec, allocation_authority, compile_foundation
from app.core.native_environments import resolve_registered_context
from app.core.allocation_models import DeploymentAllocation
from app.core.physical_capacity import StrictObservation, Digest

PHASES = Literal['pending', 'clone_intent', 'cloning', 'cloned', 'seal_intent',
                 'sealed', 'template_intent', 'templating', 'complete']


class TemplateProgress(StrictObservation):
    vm_id: int = Field(ge=1000, le=999999999)
    phase: PHASES
    vmgenid: str | None
    configuration_revision: Digest | None
    volumes: list[str] = Field(max_length=3)

    @model_validator(mode='after')
    def generation_proof(self):
        if self.vmgenid is not None and (str(uuid.UUID(self.vmgenid)) != self.vmgenid or uuid.UUID(self.vmgenid).int in {0, 2**128 - 1}):
            raise ValueError('Original generation required')
        if self.phase in {'cloned', 'seal_intent', 'sealed', 'template_intent', 'templating', 'complete'} and (
                self.vmgenid is None or self.configuration_revision is None or not self.volumes):
            raise ValueError('Original clone proof required')
        return self


class FoundationReceipt(StrictObservation):
    protocol: Literal['range42-foundation-receipt-v1']
    request_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    request_digest: Digest
    status: Literal['absent', 'preparing', 'uncertain', 'complete']
    node: NodeAttestation
    templates: list[TemplateProgress] = Field(min_length=1, max_length=16)
    foundation_ready: bool
    execution_ready: Literal[False]
    infrastructure_qualified: Literal[False]

    @field_validator('execution_ready', 'infrastructure_qualified', mode='before')
    @classmethod
    def unqualified(cls, value):
        if value is not False:
            raise ValueError('A foundation cannot qualify participant execution')
        return value

    @model_validator(mode='after')
    def bounded_ready(self):
        complete = all(row.phase == 'complete' for row in self.templates)
        uncertain = any(row.phase == 'clone_intent' for row in self.templates)
        if self.foundation_ready != complete or (self.status == 'complete') != complete or (self.status == 'uncertain') != uncertain:
            raise ValueError('Foundation progress differs from retained phases')
        if self.status == 'absent' and any(row.phase != 'pending' for row in self.templates):
            raise ValueError('Absent foundation has retained work')
        return self


def verify_node(original_node, observed_node):
    for field in ['physical_node_id', 'hardware_binding', 'node_name', 'installation_id', 'configuration_revision',
                  'api_certificate_sha256', 'node_certificate_sha256', 'supported_operations']:
        if getattr(original_node, field) != getattr(observed_node, field):
            raise ledger.refused('FOUNDATION_NODE_CHANGED', 'The original physical node or bounded preparation capability changed.')


def verified_receipt(request, value):
    try:
        parsed = FoundationReceipt.model_validate(value)
        native = request['capacity_request']
        if (parsed.request_id != native['request_id'] or parsed.request_digest != digest(request)
                or parsed.node.physical_node_id != native['commitment']['physical_node_id']
                or parsed.node.hardware_binding != native['commitment']['hardware_binding']
                or parsed.node.configuration_revision != native['root_policy_revision']
                or parsed.node.installation_id != native['installation_id']
                or parsed.node.node_name != request['plan']['target_node']
                or parsed.node.supported_operations != FOUNDATION_OPERATIONS):
            raise ValueError('Original Root request differs')
        for spec, progress in zip(request['plan']['templates'], parsed.templates, strict=True):
            if progress.vm_id != spec['vm_id']:
                raise ValueError('Original template copy differs')
            if progress.volumes:
                prefix = 'base' if progress.phase == 'complete' else 'vm'
                expected = {f'{spec["storage"]}:{prefix}-{spec["vm_id"]}-disk-0',
                            f'{spec["storage"]}:vm-{spec["vm_id"]}-cloudinit'}
                if len(spec.get('disks_gib', [32,128] if spec['role']=='proxmox' else [32])) == 2:
                    expected.add(f'{spec["storage"]}:{prefix}-{spec["vm_id"]}-disk-1')
                if set(progress.volumes) != expected or len(progress.volumes) != len(expected):
                    raise ValueError('Original copied volumes differ')
        return parsed
    except (ValueError, TypeError, KeyError):
        raise ledger.refused('FOUNDATION_RECEIPT_MISMATCH', 'The physical-node reply does not prove this original foundation. Preserve the request and reconcile its retained ownership.') from None


async def original(session, row):
    """Recompile retained ownership using the current target and environment.

    This verifies local identity; independent SSH/HTTPS observation remains a
    prerequisite before runner dispatch and completion.
    """
    ledger.commitment(row)
    if row.status != 'pending_root' or not native_capacity.root_accepted(row):
        raise ledger.refused('FOUNDATION_CAPACITY_REQUIRED', 'Retain and physically accept the original template capacity before preparation.')
    proof = native_capacity.NativeReceipt.model_validate_json(row.native_receipt_json)
    if proof.node.supported_operations != FOUNDATION_OPERATIONS:
        raise ledger.refused('FOUNDATION_CAPABILITY_REQUIRED', 'Enroll the optional bounded template preparation module on the selected physical node.')
    dep = await session.get(Deployment, row.deployment_id, populate_existing=True)
    if dep is None or not dep.native or not dep.native.get('foundation_plan'):
        raise ledger.refused()
    host = await session.get(ProxmoxHost, dep.target_host_id, populate_existing=True)
    binding = await session.get(PhysicalNodeBinding, dep.target_host_id, populate_existing=True)
    claim = await session.scalar(select(DeploymentAllocation).where(DeploymentAllocation.deployment_id == dep.id)
                                 .execution_options(populate_existing=True))
    if host is None or binding is None or claim is None:
        raise ledger.refused()
    try:
        approved = NodeAttestation.model_validate_json(binding.observation_json)
        context = await resolve_registered_context(session, dep.native['context_id'], host)
        spec = parse_spec(dep.native['foundation'])
        plan = compile_foundation(spec, context, run_id=dep.id)
        authority = allocation_authority(spec, context, host, run_id=dep.id)
        authority.validate_assignments(claim.assignments, host)
        if (plan != dep.native['foundation_plan'] or claim.id != row.allocation_id
                or (claim.host_id, claim.api_url, claim.node_name, claim.project_sha)
                != (host.id, host.api_url, host.node_name, dep.project_sha)
                or binding.target_revision != target_revision(host) or binding.agent_revision != row.agent_revision):
            raise ValueError('Original allocation changed')
        for field in ['physical_node_id', 'hardware_binding', 'node_name', 'installation_id', 'configuration_revision',
                      'api_certificate_sha256', 'node_certificate_sha256', 'supported_operations']:
            if getattr(approved, field) != getattr(proof.node, field):
                raise ValueError('Original node binding changed')
        revision = digest({'plan': plan, 'native': dep.native, 'project_id': dep.project_id,
            'project_sha': dep.project_sha, 'effective_doc_hash': dep.effective_doc_hash, 'allocation_id': claim.id})
        binding_revision = digest([await registry_revision(session), target_revision(host), binding.observation_digest, row.agent_revision])
        if revision != row.plan_revision or binding_revision != row.binding_revision:
            raise ValueError('Original reviewed plan changed')
        value = {'protocol': 'range42-foundation-request-v1',
                 'capacity_request': native_capacity.request_for(row, proof.node.installation_id), 'plan': plan}
        if row.preparation_request_json is not None and json.loads(row.preparation_request_json) != value:
            raise ValueError('Original preparation request changed')
        return value, host, context
    except (ValueError, TypeError, KeyError):
        raise ledger.refused('FOUNDATION_ORIGINAL_CHANGED', 'The original foundation, target or retained request changed. Preserve its ownership and reconcile it.') from None


async def journal(factory, reviewed):
    async with factory() as session:
        await session.execute(text('PRAGMA synchronous=FULL'))
        await session.execute(text('BEGIN IMMEDIATE'))
        row, _, _, _, _, _ = await ledger.current_lifecycle_hold(session, reviewed)
        value, _, _ = await original(session, row)
        if row.preparation_request_json is None:
            row.preparation_request_json = json.dumps(value, sort_keys=True)
            row.revision += 1
            row.record_digest = ledger.record_digest(row)
            await session.commit()
        return value


async def assert_dispatch(session, row):
    from app.core.preparation_transport import agent_configuration
    value, host, _ = await original(session, row)
    if row.preparation_request_json is None or agent_configuration(host.id, host.node_name).revision != row.agent_revision:
        raise ledger.refused('FOUNDATION_REQUEST_REQUIRED', 'Review and retain the original foundation preparation request before starting it.')
    return value


async def dispatch_inputs(factory, deployment_id):
    from app.core.preparation_transport import agent_configuration, inspect_agent, verify_api_peer
    async with factory() as session:
        row = await session.scalar(select(CapacityHold).where(CapacityHold.deployment_id == deployment_id))
        if row is None:
            raise ledger.refused()
        request = await assert_dispatch(session, row)
        _, host, _ = await original(session, row)
        config = agent_configuration(host.id, host.node_name)
        approved = native_capacity.NativeReceipt.model_validate_json(row.native_receipt_json).node
    node = await inspect_agent(config)
    verify_node(approved, node)
    await verify_api_peer(host, node)
    if agent_configuration(host.id, host.node_name).revision != config.revision:
        raise ledger.refused()
    return request, config


async def run_original(config, request, *, rpc=None, poll_interval=2):
    """Use the bounded Root stepper inside the existing native attempt."""
    from app.core.preparation_transport import read_agent
    rpc = rpc or read_agent
    async with asyncio.timeout(3600):
        while True:
            value = verified_receipt(request, await rpc(config, action='foundation-status',
                request=request, timeout=110))
            if value.status == 'complete':
                return value.model_dump()
            if value.status == 'uncertain':
                raise ledger.refused('FOUNDATION_OUTCOME_UNRESOLVED', 'The original clone reply was lost. Preserve its capacity and resolve its original native task before retrying.')
            verified_receipt(request, await rpc(config, action='foundation-prepare', request=request, timeout=110))
            await asyncio.sleep(poll_interval)


async def reconcile(factory, deployment_id):
    from app.core.preparation_transport import read_agent
    request, config = await dispatch_inputs(factory, deployment_id)
    value = verified_receipt(request, await read_agent(config, action='foundation-status', request=request, timeout=110))
    async with factory() as session:
        await session.execute(text('PRAGMA synchronous=FULL'))
        await session.execute(text('BEGIN IMMEDIATE'))
        row = await session.scalar(select(CapacityHold).where(CapacityHold.deployment_id == deployment_id))
        current, _, _ = await original(session, row)
        if current != request:
            raise ledger.refused()
        approved = native_capacity.NativeReceipt.model_validate_json(row.native_receipt_json).node
        verify_node(approved, value.node)
        if row.preparation_receipt_json is not None:
            previous = verified_receipt(request, json.loads(row.preparation_receipt_json))
            for old, new in zip(previous.templates, value.templates, strict=True):
                if (old.vmgenid is not None and old.vmgenid != new.vmgenid
                        or old.phase == 'complete' and old != new):
                    raise ledger.refused('FOUNDATION_ORIGINAL_CHANGED', 'Original prepared templates changed. Preserve their retained ownership.')
        serialized = value.model_dump_json()
        if row.preparation_receipt_json != serialized:
            row.preparation_receipt_json = serialized
            row.revision += 1
            row.record_digest = ledger.record_digest(row)
            await session.commit()
    return value
