"""Record only server-observed SSH/HTTPS bindings; this grants no capacity yet."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import or_, select, text

from app.core.errors import Range42Error
from app.core.models import ProxmoxHost
from app.core.physical_node_models import PhysicalNode, PhysicalNodeBinding

NodeOperation = Literal['inspect', 'capacity', 'capacity-admit', 'capacity-status',
                        'foundation-profile', 'foundation-prepare', 'foundation-status']
CAPACITY_OPERATIONS = ['inspect', 'capacity', 'capacity-admit', 'capacity-status']
FOUNDATION_OPERATIONS = [*CAPACITY_OPERATIONS, 'foundation-profile', 'foundation-prepare', 'foundation-status']


class NodeAttestation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    protocol: Literal["range42-preparation-agent-v1"]
    physical_node_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    hardware_binding: str = Field(pattern=r"^[0-9a-f]{64}$")
    node_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9.-]{0,127}$")
    installation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    configuration_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    api_certificate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    node_certificate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    boot_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    supported_operations: list[NodeOperation] = Field(
        min_length=1, max_length=7
    )
    execution_ready: Literal[False]
    infrastructure_qualified: Literal[False]

    @field_validator("supported_operations")
    @classmethod
    def bounded_operations(cls, value):
        if value not in [["inspect"], ["inspect", "capacity"], CAPACITY_OPERATIONS, FOUNDATION_OPERATIONS]:
            raise ValueError("Unsupported physical-node capabilities")
        return value

    @field_validator("execution_ready", "infrastructure_qualified", mode="before")
    @classmethod
    def boolean_readiness(cls, value):
        # Literal[False] otherwise also accepts the integer0 in some parsers.
        if value is not False:
            raise ValueError("Inspection cannot qualify execution")
        return value


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def target_revision(host):
    return digest([host.id, host.api_url, host.node_name, host.token_ref])


def review_required():
    return Range42Error(
        status=409,
        code="PHYSICAL_NODE_REVIEW_REQUIRED",
        message="The target or retained node registry changed. Inspect and review again before saving.",
    )


def conflict():
    return Range42Error(
        status=409,
        code="PHYSICAL_NODE_IDENTITY_CONFLICT",
        message="This physical node or target already has a different retained identity. Preserve commitments and reconcile its original binding before proceeding.",
    )


async def registry_revision(session):
    nodes = (
        await session.scalars(
            select(PhysicalNode)
            .order_by(PhysicalNode.id)
            .execution_options(populate_existing=True)
        )
    ).all()
    bindings = (
        await session.scalars(
            select(PhysicalNodeBinding)
            .order_by(PhysicalNodeBinding.host_id)
            .execution_options(populate_existing=True)
        )
    ).all()
    indexed = {row.id: row for row in nodes}
    for binding in bindings:
        try:
            stored = NodeAttestation.model_validate_json(binding.observation_json)
        except ValueError:
            raise conflict() from None
        node = indexed.get(binding.physical_node_id)
        if (
            node is None
            or (node.id, node.hardware_binding, node.node_name)
            != (stored.physical_node_id, stored.hardware_binding, stored.node_name)
            or digest(
                [binding.target_revision, binding.agent_revision, stored.model_dump()]
            )
            != binding.observation_digest
        ):
            raise conflict()
    return digest(
        {
            "nodes": [[row.id, row.hardware_binding, row.node_name] for row in nodes],
            "bindings": [
                [
                    row.host_id,
                    row.physical_node_id,
                    row.target_revision,
                    row.agent_revision,
                    row.review_revision,
                    row.observation_digest,
                ]
                for row in bindings
            ],
        }
    )


async def check_identity(session, host_id, attestation):
    nodes = (
        await session.scalars(
            select(PhysicalNode)
            .where(
                or_(
                    PhysicalNode.id == attestation.physical_node_id,
                    PhysicalNode.hardware_binding == attestation.hardware_binding,
                )
            )
            .execution_options(populate_existing=True)
        )
    ).all()
    for row in nodes:
        if (row.id, row.hardware_binding, row.node_name) != (
            attestation.physical_node_id,
            attestation.hardware_binding,
            attestation.node_name,
        ):
            raise conflict()
    binding = await session.get(PhysicalNodeBinding, host_id, populate_existing=True)
    if binding and binding.physical_node_id != attestation.physical_node_id:
        raise conflict()
    return nodes[0] if nodes else None, binding


@dataclass(frozen=True)
class ObservedNode:
    host_id: str
    target_revision: str
    attestation: NodeAttestation
    agent_revision: str
    observation_digest: str
    revision: str


async def review_observation(
    session, host_id, attestation, *, agent_revision, target_revision_at_observation
):
    """Trusted caller has already verified the forced SSH and actual HTTPS peer."""
    host = await session.get(ProxmoxHost, host_id, populate_existing=True)
    if (
        host is None
        or host.node_name != attestation.node_name
        or target_revision(host) != target_revision_at_observation
    ):
        raise review_required()
    await check_identity(session, host_id, attestation)
    target = target_revision(host)
    observation = digest([target, agent_revision, attestation.model_dump()])
    revision = digest([observation, await registry_revision(session)])
    return ObservedNode(
        host_id, target, attestation, agent_revision, observation, revision
    )


def public_binding(binding):
    attestation = NodeAttestation.model_validate_json(binding.observation_json)
    recorded = binding.recorded_at
    if recorded.tzinfo is None:
        recorded = recorded.replace(tzinfo=timezone.utc)
    return {
        "target_host_id": binding.host_id,
        "physical_node_id": binding.physical_node_id,
        "node_name": attestation.node_name,
        "hardware_binding": attestation.hardware_binding,
        "api_certificate_sha256": attestation.api_certificate_sha256,
        "node_certificate_sha256": attestation.node_certificate_sha256,
        "boot_id": attestation.boot_id,
        "recorded_at": recorded.isoformat(),
        "approved_by": binding.approved_by,
        "execution_ready": False,
        "infrastructure_qualified": False,
        "binding_recorded": True,
        "live_verified": False,
    }


async def save_observation(session, observed, *, review_revision, actor):
    """Network I/O is complete. Serialize fresh local checks without holding HTTP."""
    if not isinstance(actor, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", actor
    ):
        raise review_required()
    await session.rollback()
    await session.execute(text("BEGIN IMMEDIATE"))
    try:
        host = await session.get(ProxmoxHost, observed.host_id, populate_existing=True)
        if host is None or target_revision(host) != observed.target_revision:
            raise review_required()
        retained_revision = await registry_revision(session)
        node, binding = await check_identity(
            session, observed.host_id, observed.attestation
        )
        if binding and (binding.observation_digest, binding.review_revision) == (
            observed.observation_digest,
            review_revision,
        ):
            result = public_binding(binding)
            await session.rollback()
            return result
        current = digest([observed.observation_digest, retained_revision])
        if current != review_revision:
            raise review_required()
        now = datetime.now(timezone.utc)
        if node is None:
            node = PhysicalNode(
                id=observed.attestation.physical_node_id,
                hardware_binding=observed.attestation.hardware_binding,
                node_name=observed.attestation.node_name,
                created_at=now,
            )
            session.add(node)
            await session.flush()
        if binding is None:
            binding = PhysicalNodeBinding(
                host_id=observed.host_id, physical_node_id=node.id, created_at=now
            )
            session.add(binding)
        binding.target_revision = observed.target_revision
        binding.agent_revision = observed.agent_revision
        binding.review_revision = review_revision
        binding.observation_digest = observed.observation_digest
        binding.observation_json = observed.attestation.model_dump_json()
        binding.approved_by = actor
        binding.recorded_at = now
        await session.commit()
        return public_binding(binding)
    except BaseException:
        await session.rollback()
        raise
