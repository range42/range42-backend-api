"""Serialize server-derived capacity intents without holding a writer over RPC.

Pending intents already consume their entire commitment. Root acceptance and
execution gates are separate: this module creates no VM and grants no execution.
No attempt outcome, expiry, stopped state or missing receipt releases a hold.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import re
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
from app.core.workshop_runs import WorkshopSpec, compile_plan, resource_guests as workshop_resource_guests, validate_activation as validate_workshop_activation

# A locally terminal runner can still have an uncertain native outcome.
CAPACITY_SETTLED_ATTEMPT_STATES = tuple(state for state in TERMINAL_ATTEMPT_STATES if state not in {'unknown', 'partial'})


def resource_guests(plan):
    return plan['templates'] if plan.get('kind') in {'native-workshop-foundation','native-template-preparation'} else workshop_resource_guests(plan)


def validate_activation(plan):
    if plan.get('kind') not in {'native-workshop-foundation','native-template-preparation'}:
        validate_workshop_activation(plan)


def refused(
    code="CAPACITY_REVIEW_REQUIRED",
    message="The capacity registry, original binding or server plan changed. Preserve existing holds and review configured resources again.",
):
    return Range42Error(status=409, code=code, message=message)


def record_digest(row):
    lifecycle = [row.native_request_id, row.native_request_actor,
                 row.retired_by, row.retirement_approval_revision]
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
        ] + (['native-lifecycle-v1', *lifecycle] if any(value is not None for value in lifecycle) else [])
        + (['native-receipt-v1', row.native_receipt_json] if row.native_receipt_json is not None else [])
        + (['native-preparation-v1', row.preparation_request_json, row.preparation_receipt_json]
           if row.preparation_request_json is not None or row.preparation_receipt_json is not None else [])
    )


def commitment(row):
    try:
        value = CapacityCommitment.model_validate_json(row.record_json)
        if (
            row.status not in {"pending_root", "active", "releasing", "retired"}
            or (row.native_request_id is None) != (row.native_request_actor is None)
            or (row.preparation_receipt_json is not None and row.preparation_request_json is None)
            or (row.native_request_id is not None and not re.fullmatch(r'[a-f0-9]{32}', row.native_request_id))
            or (row.retired_by is None) != (row.retirement_approval_revision is None)
            or (row.retired_by is not None and row.status != 'retired')
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
        if dep.native.get('foundation_plan'):
            from app.core.template_preparation import parse_spec, compile_foundation
            from app.core.native_environments import resolve_registered_context
            if dep.native.get('workshop') or dep.native.get('workshop_plan'):
                raise ValueError('Foundation and run ownership cannot be combined')
            context = await resolve_registered_context(session, dep.native['context_id'], host)
            plan = compile_foundation(parse_spec(dep.native['foundation']), context, run_id=dep.id)
            retained_plan = dep.native['foundation_plan']
        else:
            spec = WorkshopSpec.model_validate(dep.native["workshop"])
            plan = compile_plan(spec, run_id=dep.id)
            retained_plan = dep.native['workshop_plan']
        if (
            plan != retained_plan
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
            "supported_operations",
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
                                "logical_bytes": guest.get('logical_bytes', guest["disk_gib"] * 2**30),
                            }
                        ],
                    }
                    for guest in resource_guests(plan)
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
    unused_retirement_eligible: bool = False

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
            bool(existing and existing.status == 'pending_root' and existing.native_request_id is None
                 and not await work_in_progress(session, deployment_id)
                 and not guest_present(observed, candidate)),
        )


def guest_present(observed, candidate):
    return bool({guest.vm_id for guest in observed.resources.guests}
                & {guest.vm_id for guest in candidate.guests})


async def work_in_progress(session, deployment_id):
    deployment = await session.get(Deployment, deployment_id, populate_existing=True)
    attempt = await session.scalar(select(Attempt.id).where(
        Attempt.deployment_id == deployment_id,
        Attempt.state.not_in(CAPACITY_SETTLED_ATTEMPT_STATES)))
    return (deployment is None or deployment.state == 'unknown' or attempt is not None
            or await session.get(WorkspaceLock, deployment_id) is not None)


async def current_lifecycle_hold(session, reviewed):
    candidate, plan_revision, binding_revision, plan = await demand(
        session, reviewed.deployment_id, reviewed.observed, reviewed.agent_revision)
    rows, _, revision = await retained(session)
    row = next((row for row in rows if row.deployment_id == reviewed.deployment_id), None)
    if row is None or row.status != 'pending_root':
        raise refused()
    saved = commitment(row)
    expected = saved.model_copy(update={'guests': [guest.model_copy(update={'materialization': None}) for guest in saved.guests]})
    if (row.plan_revision != plan_revision or row.binding_revision != binding_revision
            or expected != candidate):
        raise refused()
    return row, candidate, revision, plan_revision, binding_revision, plan


def assert_fresh_lifecycle_review(reviewed, revision, plan_revision, binding_revision):
    if (revision != reviewed.registry_revision or plan_revision != reviewed.plan_revision
            or binding_revision != reviewed.binding_revision
            or not 0 <= (datetime.now(timezone.utc) - reviewed.observed_at).total_seconds() <= 120):
        raise refused()


async def journal_native_request(factory, reviewed, *, actor, expected_revision):
    """Persist an original request BEFORE any future native RPC.

    The fixed native acceptance adapter consumes this journal; it grants no
    execution. Once present, treat the RPC as possibly sent until independent
    native reconciliation proves its outcome; a timeout never clears it.
    """
    async with factory() as session:
        # NORMAL WAL commits can be lost after power loss even though a remote
        # native commit already escaped. Seal this journal before any RPC.
        await session.execute(text('PRAGMA synchronous=FULL'))
        await session.execute(text('BEGIN IMMEDIATE'))
        row, candidate, revision, plan_revision, binding_revision, plan = await current_lifecycle_hold(session, reviewed)
        if row.approval_revision != expected_revision:
            raise refused()
        if row.native_request_id is not None:
            return row
        assert_fresh_lifecycle_review(reviewed, revision, plan_revision, binding_revision)
        validate_activation(plan)
        if guest_present(reviewed.observed, candidate) or await work_in_progress(session, reviewed.deployment_id):
            raise refused('CAPACITY_WORK_IN_PROGRESS', 'Resolve original guest or uncertain execution state before requesting native acceptance.')
        row.native_request_id = uuid.uuid4().hex
        row.native_request_actor = actor
        row.revision += 1
        row.record_digest = record_digest(row)
        await session.commit()
        return row


async def retire_unused(factory, reviewed, *, actor, expected_revision):
    """Retire only a never-dispatched, independently absent, idle intent.

    Does not delete any guest, provider identity or allocation. Materialized,
    active or possibly dispatched intents require the future native retirement
    adapter. Keep original intent/audit records after releasing the commitment.
    """
    async with factory() as session:
        await session.execute(text('BEGIN IMMEDIATE'))
        rows, _, _ = await retained(session)
        previous = next((row for row in rows if row.deployment_id == reviewed.deployment_id), None)
        if previous and previous.status == 'retired':
            if previous.retirement_approval_revision == expected_revision:
                return previous
            raise refused()
        row, candidate, revision, plan_revision, binding_revision, _ = await current_lifecycle_hold(session, reviewed)
        if row.native_request_id is not None:
            raise refused('CAPACITY_NATIVE_RECONCILIATION_REQUIRED',
                          'Native acceptance may have been requested. Preserve this commitment until the original native request is reconciled and retired.')
        assert_fresh_lifecycle_review(reviewed, revision, plan_revision, binding_revision)
        if expected_revision != reviewed.revision:
            raise refused()
        if guest_present(reviewed.observed, candidate) or await work_in_progress(session, reviewed.deployment_id):
            raise refused('CAPACITY_WORK_IN_PROGRESS',
                          'Original guests or uncertain work still exist. No capacity was released.')
        saved = commitment(row).model_copy(update={'phase': 'retired'})
        row.status = 'retired'
        row.record_json = saved.model_dump_json()
        row.retired_at = datetime.now(timezone.utc)
        row.retired_by = actor
        row.retirement_approval_revision = expected_revision
        row.revision += 1
        row.record_digest = record_digest(row)
        await session.commit()
        return row


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
        if await work_in_progress(session, reviewed.deployment_id):
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
    deployment = await session.get(Deployment, deployment_id, populate_existing=True)
    if deployment and deployment.native and deployment.native.get('foundation_plan'):
        from app.core.foundation_execution import assert_dispatch
        await assert_dispatch(session, row)
        from app.core.environment_network_execution import assert_dispatch as assert_network_dispatch
        await assert_network_dispatch(session, deployment_id)
        return
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
            native.get(field) is not None for field in ["workshop_plan", "workshop", "foundation_plan", "foundation"]
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
        deployment = await session.get(Deployment, owners[0].deployment_id, populate_existing=True)
        if deployment and deployment.native and deployment.native.get('foundation_plan'):
            raise refused('FOUNDATION_BOUNDED_OPERATION_REQUIRED',
                'Prepared template copies accept only their original bounded preparation action. General VM changes remain blocked.')
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
