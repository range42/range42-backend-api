"""Serialize server-derived capacity intents without holding a writer over RPC.

Pending intents already consume their entire commitment. Root acceptance and
execution gates are separate: this module creates no VM and grants no execution.
No attempt outcome, expiry, stopped state or missing receipt releases a hold.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import uuid

from sqlalchemy import select, text

from app.core.allocation_models import DeploymentAllocation
from app.core.capacity_accounting import CapacityCommitment, assess
from app.core.capacity_models import CapacityHold
from app.core.errors import Range42Error
from app.core.models import Attempt, Deployment, ProxmoxHost, WorkspaceLock
from app.core.attempt_lifecycle import TERMINAL_ATTEMPT_STATES
from app.core.physical_node_models import PhysicalNodeBinding
from app.core.physical_nodes import (
    NodeAttestation,
    digest,
    registry_revision,
    target_revision,
)
from app.core.workshop_runs import WorkshopSpec, compile_plan, validate_activation


def refused(
    code="CAPACITY_REVIEW_REQUIRED",
    message="The capacity registry, original binding or server plan changed. Preserve existing holds and review configured resources again.",
):
    return Range42Error(status=409, code=code, message=message)


def record_digest(row):
    return digest(
        [
            row.id,
            row.deployment_id,
            row.allocation_id,
            row.physical_node_id,
            row.status,
            row.record_json,
            row.plan_revision,
            row.binding_revision,
            row.agent_revision,
            row.root_policy_revision,
            row.approval_revision,
            row.approved_by,
            row.revision,
        ]
    )


def commitment(row):
    try:
        value = CapacityCommitment.model_validate_json(row.record_json)
        if (
            row.status not in {"pending_root", "active", "releasing", "retired"}
            or type(row.revision) is not int
            or row.revision < 1
            or row.record_digest != record_digest(row)
            or (
                value.id,
                value.deployment_id,
                value.allocation_id,
                value.physical_node_id,
            )
            != (row.id, row.deployment_id, row.allocation_id, row.physical_node_id)
            or value.phase != ("retired" if row.status == "retired" else "held")
        ):
            raise ValueError("Retained hold differs")
        return value
    except (ValueError, TypeError):
        raise refused(
            "CAPACITY_REGISTRY_CONFLICT",
            "A retained capacity intent is incomplete or changed. Restore its original record; no commitment was released.",
        ) from None


async def retained(session):
    rows = list(
        (
            await session.scalars(
                select(CapacityHold)
                .order_by(CapacityHold.id)
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    records = [commitment(row) for row in rows]
    return rows, records, digest([[row.id, row.record_digest] for row in rows])


async def demand(session, deployment_id, observed, agent_revision):
    dep = await session.get(Deployment, deployment_id, populate_existing=True)
    if dep is None or not isinstance(dep.native, dict):
        raise refused()
    host = await session.get(ProxmoxHost, dep.target_host_id, populate_existing=True)
    binding = await session.get(
        PhysicalNodeBinding, dep.target_host_id, populate_existing=True
    )
    claim = await session.scalar(
        select(DeploymentAllocation)
        .where(DeploymentAllocation.deployment_id == dep.id)
        .execution_options(populate_existing=True)
    )
    if host is None or binding is None or claim is None:
        raise refused()
    physical_registry = await registry_revision(session)
    try:
        approved = NodeAttestation.model_validate_json(binding.observation_json)
        spec = WorkshopSpec.model_validate(dep.native["workshop"])
        plan = compile_plan(spec, run_id=dep.id)
        if (
            plan != dep.native["workshop_plan"]
            or claim.assignments != plan["assignments"]
            or (claim.host_id, claim.api_url, claim.node_name, claim.project_sha)
            != (host.id, host.api_url, host.node_name, dep.project_sha)
            or binding.target_revision != target_revision(host)
            or binding.agent_revision != agent_revision
        ):
            raise ValueError("Server plan or original target differs")
        for field in [
            "physical_node_id",
            "hardware_binding",
            "node_name",
            "installation_id",
            "configuration_revision",
            "api_certificate_sha256",
            "node_certificate_sha256",
        ]:
            if getattr(approved, field) != getattr(observed.node, field):
                raise ValueError("Original node binding differs")
        backings = [
            row for row in observed.resources.storage if row.storage == plan["storage"]
        ]
        if len(backings) != 1:
            raise ValueError("Approved storage backing is unavailable")
        backing = backings[0]
        value = CapacityCommitment.model_validate(
            {
                "id": uuid.uuid5(
                    uuid.NAMESPACE_URL, "range42-capacity:" + claim.id
                ).hex,
                "deployment_id": dep.id,
                "allocation_id": claim.id,
                "physical_node_id": observed.node.physical_node_id,
                "hardware_binding": observed.node.hardware_binding,
                "phase": "held",
                "guests": [
                    {
                        "vm_id": guest["vm_id"],
                        "memory_mib": guest["memory_mib"],
                        "vcpus": guest["cores"],
                        "materialization": None,
                        "storage": [
                            {
                                "pool_id": backing.pool_id,
                                "cluster_id": backing.cluster_id,
                                "namespace": backing.namespace,
                                "replicas": backing.replicas,
                                "logical_bytes": guest["disk_gib"] * 2**30,
                            }
                        ],
                    }
                    for guest in plan["guests"]
                ],
            }
        )
    except (ValueError, TypeError, KeyError):
        raise refused() from None
    return (
        value,
        digest(
            {
                "plan": plan,
                "native": dep.native,
                "project_id": dep.project_id,
                "project_sha": dep.project_sha,
                "effective_doc_hash": dep.effective_doc_hash,
                "allocation_id": claim.id,
            }
        ),
        digest(
            [
                physical_registry,
                target_revision(host),
                binding.observation_digest,
                agent_revision,
            ]
        ),
        plan,
    )


@dataclass(frozen=True)
class CapacityReviewWitness:
    deployment_id: str
    observed: object
    agent_revision: str
    candidate: CapacityCommitment
    registry_revision: str
    plan_revision: str
    binding_revision: str
    assessment: dict
    observed_at: datetime

    @property
    def revision(self):
        stable = self.observed.model_dump()
        # Shared-pool free-space samples change continuously. Recheck them
        # during reservation, while retaining approval of exact identities,
        # configured limits, original guests/volumes and competing holds.
        for storage in stable["resources"]["storage"]:
            storage.pop("logical_available_bytes")
            storage.pop("cluster_raw_available_bytes")
        return digest(
            [
                self.deployment_id,
                self.candidate.model_dump(),
                self.registry_revision,
                self.plan_revision,
                self.binding_revision,
                self.agent_revision,
                stable,
            ]
        )


async def review(factory, deployment_id, observed, *, agent_revision):
    async with factory() as session:
        candidate, plan_revision, binding_revision, _ = await demand(
            session, deployment_id, observed, agent_revision
        )
        rows, records, revision = await retained(session)
        existing = next(
            (row for row in rows if row.deployment_id == deployment_id), None
        )
        if existing:
            saved = commitment(existing)
            if existing.plan_revision != plan_revision or saved.model_dump(
                exclude={"guests"}
            ) != candidate.model_dump(exclude={"guests"}):
                raise refused()
            assessment = assess(observed, records, None)
        else:
            assessment = assess(observed, records, candidate)
        return CapacityReviewWitness(
            deployment_id,
            observed,
            agent_revision,
            candidate,
            revision,
            plan_revision,
            binding_revision,
            assessment,
            datetime.now(timezone.utc),
        )


async def reserve(factory, reviewed, *, actor, expected_revision=None):
    # Network observation belongs to the caller before entering this writer.
    # The HTTP contract must accept only an opaque server review, never this
    # internal object or caller-provided resource quantities/receipts.
    async with factory() as session:
        await session.execute(text("BEGIN IMMEDIATE"))
        requested = (
            expected_revision if expected_revision is not None else reviewed.revision
        )
        candidate, plan_revision, binding_revision, plan = await demand(
            session, reviewed.deployment_id, reviewed.observed, reviewed.agent_revision
        )
        rows, records, revision = await retained(session)
        existing = next(
            (row for row in rows if row.deployment_id == reviewed.deployment_id), None
        )
        if existing:
            saved = commitment(existing)
            expected = saved.model_dump()
            expected["guests"] = [
                {**guest, "materialization": None} for guest in expected["guests"]
            ]
            if (
                existing.status == "retired"
                or existing.approval_revision != requested
                or existing.plan_revision != plan_revision
                or existing.binding_revision != binding_revision
                or CapacityCommitment.model_validate(expected) != candidate
            ):
                raise refused()
            return existing
        deployment = await session.get(Deployment, reviewed.deployment_id)
        running = await session.scalar(
            select(Attempt.id).where(
                Attempt.deployment_id == reviewed.deployment_id,
                Attempt.state.not_in(TERMINAL_ATTEMPT_STATES),
            )
        )
        if (
            deployment.state == "unknown"
            or running is not None
            or await session.get(WorkspaceLock, reviewed.deployment_id)
        ):
            raise refused(
                "CAPACITY_WORK_IN_PROGRESS",
                "Resolve running or uncertain work before creating a new capacity intent. Existing commitments remain retained.",
            )
        if (
            requested != reviewed.revision
            or revision != reviewed.registry_revision
            or plan_revision != reviewed.plan_revision
            or binding_revision != reviewed.binding_revision
            or candidate != reviewed.candidate
            or not 0
            <= (datetime.now(timezone.utc) - reviewed.observed_at).total_seconds()
            <= 120
        ):
            raise refused()
        validate_activation(plan)
        assessment = assess(reviewed.observed, records, candidate)
        if not assessment["admissible"]:
            raise refused(
                "CAPACITY_EXHAUSTED",
                "Configured resources and retained intents exhaust this physical node or shared storage budget. Release capacity through its verified lifecycle or reduce the deployment size.",
            )
        now = datetime.now(timezone.utc)
        row = CapacityHold(
            id=candidate.id,
            deployment_id=candidate.deployment_id,
            allocation_id=candidate.allocation_id,
            physical_node_id=candidate.physical_node_id,
            status="pending_root",
            record_json=candidate.model_dump_json(),
            record_digest="",
            plan_revision=plan_revision,
            binding_revision=binding_revision,
            agent_revision=reviewed.agent_revision,
            root_policy_revision=reviewed.observed.node.configuration_revision,
            approval_revision=requested,
            approved_by=actor,
            revision=1,
            created_at=now,
        )
        row.record_digest = record_digest(row)
        session.add(row)
        await session.commit()
        return row


async def assert_execution_accepted(session, deployment_id):
    row = await session.scalar(
        select(CapacityHold)
        .where(CapacityHold.deployment_id == deployment_id)
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise refused(
            "CAPACITY_HOLD_REQUIRED",
            "Reserve this run’s original configured resources before starting it.",
        )
    commitment(row)
    if row.status == "retired":
        raise refused(
            "CAPACITY_RETIRED",
            "This capacity intent was retired. Create a new run instead of reviving released commitments.",
        )
    # A database phase is not a native execution attestation. Keep this gate
    # closed until the original root plan receipt and bounded executor are
    # implemented and independently qualified. Browser fields cannot open it.
    raise refused(
        "CAPACITY_ROOT_ACCEPTANCE_REQUIRED",
        "The original physical-node acceptance and bounded execution adapter are not qualified for this intent. Capacity remains reserved; no guest operation was sent.",
    )


async def assert_allocation_releasable(session, claim):
    row = await session.scalar(
        select(CapacityHold)
        .where(CapacityHold.deployment_id == claim.deployment_id)
        .execution_options(populate_existing=True)
    )
    if row is None:
        return
    commitment(row)
    if row.allocation_id != claim.id or row.status != "retired":
        raise refused(
            "CAPACITY_RETIREMENT_REQUIRED",
            "Reconcile and retire the original native capacity intent before releasing its allocation. Pending or uncertain commitments remain retained.",
        )


async def assert_dispatch_accepted(session, deployment_id):
    deployment = await session.get(Deployment, deployment_id, populate_existing=True)
    row = await session.scalar(
        select(CapacityHold).where(CapacityHold.deployment_id == deployment_id)
    )
    native = deployment.native if deployment else None
    if (
        row is not None
        or isinstance(native, dict)
        and any(
            native.get(field) is not None for field in ["workshop_plan", "workshop"]
        )
    ):
        await assert_execution_accepted(session, deployment_id)
        return
    _, records, _ = await retained(session)
    if any(record.phase == "held" for record in records):
        raise refused(
            "CAPACITY_PLAN_REQUIRED",
            "This installation retains physical capacity commitments. Other provisioning needs an independently scoped capacity plan before it can run.",
        )


async def assert_vm_capacity_accepted(session, vmid):
    _, records, _ = await retained(session)
    owners = [
        record
        for record in records
        if record.phase == "held"
        and any(guest.vm_id == vmid for guest in record.guests)
    ]
    if len(owners) > 1:
        raise refused(
            "CAPACITY_REGISTRY_CONFLICT",
            "This guest has ambiguous retained capacity ownership. Preserve both records and reconcile the original allocation.",
        )
    if owners:
        await assert_execution_accepted(session, owners[0].deployment_id)
        return
    if any(record.phase == "held" for record in records):
        raise refused(
            "CAPACITY_PLAN_REQUIRED",
            "Unplanned VM writes cannot bypass retained capacity commitments. Use an independently scoped capacity plan before changing these resources.",
        )


async def assert_unscoped_writes_accepted(session):
    _, records, _ = await retained(session)
    if any(record.phase == "held" for record in records):
        raise refused(
            "CAPACITY_UNSCOPED_WRITE_PAUSED",
            "Unscoped legacy writes cannot run alongside retained physical capacity intents. Use the original scoped deployment workflow.",
        )
