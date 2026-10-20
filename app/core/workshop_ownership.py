"""Adopt an original legacy cohort into the one canonical allocation ledger.

Trusted callers obtain fresh restricted-controller and verified HTTPS evidence.
Only an opaque review is accepted from a browser. Controller handoff never runs
VM/provider operations, auto-resumes admission, or grants physical capacity.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Literal
from ipaddress import IPv4Address
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator
from sqlalchemy import select, text

from app.core.allocation_models import AllocationReservation, DeploymentAllocation
from app.core.attempt_lifecycle import TERMINAL_ATTEMPT_STATES
from app.core.config import Settings
from app.core.deployment_allocations import bind, validate_binding
from app.core.errors import Range42Error
from app.core.locks import ProvisioningLock
from app.core.models import Deployment, ProxmoxHost, Attempt, WorkspaceLock
from app.core.physical_nodes import digest, target_revision as physical_target_revision
from app.core.workshop_ownership_models import WorkshopOwnershipImport


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Cohort(StrictModel):
    id: str
    project_id: str
    project_sha: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    target_host_id: str


class Generation(StrictModel):
    owner: str = Field(max_length=128)
    vmgenid: str = Field(pattern=r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
    receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class Nic(StrictModel):
    index: int = Field(ge=0, le=31)
    bridge: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,14}$")
    ip: str
    prefix: int = Field(ge=1, le=32)

    @field_validator("ip")
    @classmethod
    def ipv4(cls, value):
        return str(IPv4Address(value))


class Assignment(StrictModel):
    instance_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    vm_id: int = Field(ge=100, le=999999999)
    vm_name: str = Field(min_length=1, max_length=128)
    nics: list[Nic] = Field(max_length=32)
    generation: Generation


class Handoff(StrictModel):
    import_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    phase: Literal["pending", "acknowledged"]
    review_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    allocation_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


class OwnershipWitness(StrictModel):
    version: Literal[1]
    cohort: Cohort
    node_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9.-]{0,127}$")
    api_certificate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    node_certificate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    assignments: list[Assignment] = Field(min_length=1, max_length=256)
    review_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    handoff: Handoff | None = None

    @field_validator("version", mode="before")
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError("Protocol version must be an integer")
        return value

    @model_validator(mode="after")
    def original_scope(self):
        ids = [row.vm_id for row in self.assignments]
        addresses = [
            (nic.bridge, nic.ip) for row in self.assignments for nic in row.nics
        ]
        if len(set(ids)) != len(ids) or len(set(addresses)) != len(addresses):
            raise ValueError("Duplicate original VM or network address")
        for row in self.assignments:
            if [nic.index for nic in row.nics] != list(range(len(row.nics))):
                raise ValueError("Original NICs must have contiguous explicit indices")
        if self.review_revision != digest(
            self.model_dump(exclude={"review_revision", "handoff"})
        ):
            raise ValueError("The controller ownership evidence changed")
        return self


def refused(code="WORKSHOP_OWNERSHIP_REVIEW_REQUIRED"):
    return Range42Error(
        status=409,
        code=code,
        message="Original workshop ownership needs review or recovery. Preserve its retained handoff and allocation before retrying.",
    )


def target_revision(host):
    return digest([physical_target_revision(host), host.protected_vmids_override_json])


async def binding(session, deployment_id):
    dep = await session.get(Deployment, deployment_id, populate_existing=True)
    if dep is None:
        raise Range42Error(
            status=404, code="NOT_FOUND", message="Deployment not found."
        )
    host = await session.get(ProxmoxHost, dep.target_host_id, populate_existing=True)
    native = dep.native or {}
    declared = native.get("descriptor", {}).get("declared_vmids")
    if (
        host is None
        or not dep.project_sha
        or not isinstance(native.get("path"), str)
        or not isinstance(declared, list)
        or not 1 <= len(declared) <= 4096
        or any(
            type(vmid) is not int or not 100 <= vmid <= 999999999 for vmid in declared
        )
    ):
        raise refused()

    revision = digest(
        [
            dep.id,
            dep.project_id,
            dep.project_sha,
            dep.target_host_id,
            native,
            target_revision(host),
        ]
    )
    return dep, host, revision


async def registry_revision(session):
    claims = (
        await session.scalars(
            select(DeploymentAllocation)
            .order_by(DeploymentAllocation.id)
            .execution_options(populate_existing=True)
        )
    ).all()
    leases = (
        await session.scalars(
            select(AllocationReservation)
            .order_by(AllocationReservation.id)
            .execution_options(populate_existing=True)
        )
    ).all()
    return digest(
        [
            [row.id, row.host_id, row.node_name, row.assignments]
            for row in [*claims, *leases]
        ]
    )


@dataclass(frozen=True)
class Observation:
    deployment_id: str
    target_revision: str
    binding_revision: str
    witness: OwnershipWitness
    revision: str


async def review_observation(
    session, deployment_id, witness, *, observed_target_revision
):
    dep, host, revision = await binding(session, deployment_id)
    expected = Cohort(
        id=dep.id,
        project_id=dep.project_id,
        project_sha=dep.project_sha,
        target_host_id=dep.target_host_id,
    )
    if (
        witness.cohort != expected
        or witness.node_name != host.node_name
        or observed_target_revision != target_revision(host)
        or not {row.vm_id for row in witness.assignments}
        <= set(dep.native["descriptor"]["declared_vmids"])
    ):
        raise refused()
    review = digest(
        [revision, witness.review_revision, await registry_revision(session)]
    )
    return Observation(
        deployment_id, observed_target_revision, revision, witness, review
    )


def topology(witness):
    return [row.model_dump(exclude={"generation"}) for row in witness.assignments]


async def _record(session, deployment_id):
    return await session.scalar(
        select(WorkshopOwnershipImport)
        .where(WorkshopOwnershipImport.deployment_id == deployment_id)
        .execution_options(populate_existing=True)
    )


async def _valid_record(session, row):
    try:
        witness = OwnershipWitness.model_validate_json(row.witness_json)
        dep, host, revision = await binding(session, row.deployment_id)
        if (
            row.phase not in {"pending_controller", "claim_saved", "active", "released"}
            or not re.fullmatch("[0-9a-f]{32}", row.id)
            or row.ownership_revision != witness.review_revision
            or row.binding_revision != revision
            or witness.cohort.id != row.deployment_id
        ):
            raise ValueError()
        claim = await session.scalar(
            select(DeploymentAllocation)
            .where(DeploymentAllocation.deployment_id == dep.id)
            .execution_options(populate_existing=True)
        )
        if row.phase in {"claim_saved", "active"}:
            if (
                claim is None
                or claim.id != row.allocation_id
                or claim.assignments != topology(witness)
            ):
                raise ValueError()
            validate_binding(claim, dep, host)
        elif row.phase == "pending_controller" and (
            row.allocation_id is not None or claim is not None
        ):
            raise ValueError()
        elif row.phase == "released" and claim is not None:
            raise ValueError()
        if row.phase == "active":
            receipt = Handoff.model_validate_json(row.controller_receipt_json)
            if (
                receipt.import_id,
                receipt.phase,
                receipt.review_revision,
                receipt.allocation_id,
            ) != (row.id, "acknowledged", row.ownership_revision, row.allocation_id):
                raise ValueError()
        return witness, claim
    except (ValueError, TypeError, KeyError, Range42Error):
        raise refused() from None


async def status(session, deployment_id):
    row = await _record(session, deployment_id)
    if row is None:
        await binding(session, deployment_id)
        return {
            "deployment_id": deployment_id,
            "phase": "unimported",
            "import_id": None,
            "allocation_id": None,
            "ownership_revision": None,
            "project_sha": None,
            "target_host_id": None,
            "assignments": [],
            "resume_review_revision": None,
            "changed_by": None,
            "capacity_borrowing_allowed": False,
            "remote_quiescence_verified": False,
        }
    witness, claim = await _valid_record(session, row)
    return {
        "deployment_id": deployment_id,
        "phase": row.phase,
        "import_id": row.id,
        "allocation_id": row.allocation_id,
        "ownership_revision": row.ownership_revision,
        "project_sha": witness.cohort.project_sha,
        "target_host_id": witness.cohort.target_host_id,
        "assignments": claim.assignments if claim else topology(witness),
        "resume_review_revision": row.review_revision,
        "changed_by": row.changed_by,
        "capacity_borrowing_allowed": False,
        "remote_quiescence_verified": False,
    }


async def _idle(session, deployment_id):
    active = await session.scalar(
        select(Attempt.id).where(
            Attempt.deployment_id == deployment_id,
            Attempt.state.not_in(TERMINAL_ATTEMPT_STATES),
        )
    )
    if active or await session.get(WorkspaceLock, deployment_id):
        raise refused()


def validated_handoff(value):
    try:
        return Handoff.model_validate(value)
    except ValueError:
        # Validation diagnostics may contain an unexpected private RPC field.
        raise refused() from None


async def save_observation(session, observed, *, review_revision, actor, rpc):
    if not isinstance(actor, str) or not re.fullmatch(
        "[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", actor
    ):
        raise refused()
    with ProvisioningLock(Settings().workspace_root / ".locks"):
        await session.rollback()
        await session.execute(text("BEGIN IMMEDIATE"))
        try:
            dep, host, current = await binding(session, observed.deployment_id)
            if (
                current != observed.binding_revision
                or target_revision(host) != observed.target_revision
            ):
                raise refused()
            row = await _record(session, dep.id)
            if row:
                await _valid_record(session, row)
                if (
                    row.review_revision != review_revision
                    or row.ownership_revision != observed.witness.review_revision
                    or row.phase == "released"
                ):
                    raise refused()
                if row.phase == "active":
                    result = await status(session, dep.id)
                    await session.rollback()
                    return result
            else:
                if (
                    digest(
                        [
                            current,
                            observed.witness.review_revision,
                            await registry_revision(session),
                        ]
                    )
                    != review_revision
                ):
                    raise refused()
                if await session.scalar(
                    select(DeploymentAllocation.id).where(
                        DeploymentAllocation.deployment_id == dep.id
                    )
                ):
                    raise refused()
                await _idle(session, dep.id)
                instant = datetime.now(timezone.utc)
                row = WorkshopOwnershipImport(
                    id=uuid.uuid4().hex,
                    deployment_id=dep.id,
                    phase="pending_controller",
                    review_revision=review_revision,
                    ownership_revision=observed.witness.review_revision,
                    binding_revision=current,
                    witness_json=observed.witness.model_dump_json(),
                    changed_by=actor,
                    created_at=instant,
                    updated_at=instant,
                )
                session.add(row)
            identifier, phase, dep_id = row.id, row.phase, dep.id
            await (
                session.commit()
            )  # Durable intent precedes the first remote metadata write.
            if phase == "pending_controller":
                receipt = validated_handoff(
                    await rpc(
                        {
                            "action": "ownership-begin",
                            "import_id": identifier,
                            "review_revision": observed.witness.review_revision,
                        }
                    )
                )
                if (
                    receipt.import_id,
                    receipt.phase,
                    receipt.review_revision,
                    receipt.allocation_id,
                ) != (identifier, "pending", observed.witness.review_revision, None):
                    raise refused()
                await session.execute(text("BEGIN IMMEDIATE"))
                row = await _record(session, dep_id)
                await _valid_record(session, row)
                dep, host, current = await binding(session, dep_id)
                if (
                    current != observed.binding_revision
                    or row.id != identifier
                    or row.phase != "pending_controller"
                ):
                    raise refused()
                await _idle(session, dep_id)
                claim = await bind(session, dep, host, topology(observed.witness))
                await session.flush()
                claim.source_assignments = observed.witness.model_dump()["assignments"]
                row.allocation_id = claim.id
                row.phase = "claim_saved"
                row.updated_at = datetime.now(timezone.utc)
                await (
                    session.commit()
                )  # Callback reads may now verify the actual canonical claim.
            receipt = validated_handoff(
                await rpc({"action": "ownership-commit", "import_id": identifier})
            )
            await session.execute(text("BEGIN IMMEDIATE"))
            row = await _record(session, dep_id)
            await _valid_record(session, row)
            if (
                row.id,
                row.phase,
                receipt.import_id,
                receipt.phase,
                receipt.review_revision,
                receipt.allocation_id,
            ) != (
                identifier,
                "claim_saved",
                identifier,
                "acknowledged",
                row.ownership_revision,
                row.allocation_id,
            ):
                raise refused()
            row.controller_receipt_json = receipt.model_dump_json()
            row.phase = "active"
            row.updated_at = datetime.now(timezone.utc)
            await session.flush()
            result = await status(session, dep_id)
            await session.commit()
            return result
        except BaseException:
            await session.rollback()
            raise


async def assert_releasable(session, claim):
    row = await _record(session, claim.deployment_id)
    if row is not None:
        await _valid_record(session, row)
        if row.phase != "active" or row.allocation_id != claim.id:
            raise refused()


async def record_release(session, claim):
    """Called only after the existing canonical independent-absence gate."""
    row = await _record(session, claim.deployment_id)
    if row is not None:
        await assert_releasable(session, claim)
        row.phase = "released"
        row.updated_at = datetime.now(timezone.utc)
