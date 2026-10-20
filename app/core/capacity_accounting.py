"""Conservative resource arithmetic for server-created durable commitments.

These models are internal ledger records, never an HTTP admission body. Only a
canonical, independently verified native receipt may populate materialization.
Assessment is not a capacity grant; persistence must serialize its result with
all competing holds and validate the unchanged physical/target/plan binding.
"""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.core.physical_capacity import Digest, StrictObservation

Quantity = Annotated[int, Field(ge=0, le=2**63 - 1)]
Identity = Annotated[str, Field(min_length=1, max_length=64)]
Namespace = Annotated[str, Field(pattern=r"^(?:[A-Za-z0-9][A-Za-z0-9_.-]{0,63})?$")]


class HeldStorage(StrictObservation):
    pool_id: Digest
    cluster_id: Digest
    namespace: Namespace
    replicas: int = Field(ge=1, le=64)
    logical_bytes: Quantity


class OriginalVolume(StrictObservation):
    pool_id: Digest
    namespace: Namespace
    volume: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_./-]{0,255}$")
    size_bytes: Quantity


class MaterializationReceipt(StrictObservation):
    vmgenid: str = Field(pattern=r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
    configuration_revision: Digest
    receipt_sha256: Digest
    volumes: list[OriginalVolume] = Field(max_length=256)

    @model_validator(mode="after")
    def unique_volumes(self):
        keys = [(row.pool_id, row.namespace, row.volume) for row in self.volumes]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate original volume identity")
        return self


class HeldGuest(StrictObservation):
    vm_id: int = Field(ge=100, le=999999999)
    memory_mib: int = Field(ge=1, le=2**30)
    vcpus: int = Field(ge=1, le=65536)
    storage: list[HeldStorage] = Field(max_length=16)
    materialization: MaterializationReceipt | None

    @model_validator(mode="after")
    def unique_backings(self):
        keys = [(row.pool_id, row.namespace) for row in self.storage]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate guest storage commitment")
        return self


class CapacityCommitment(StrictObservation):
    id: Identity
    allocation_id: Identity
    deployment_id: Identity
    physical_node_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    hardware_binding: Digest
    phase: Literal["held", "retired"]
    guests: list[HeldGuest] = Field(min_length=1, max_length=1024)

    @model_validator(mode="after")
    def unique_guests(self):
        if len({guest.vm_id for guest in self.guests}) != len(self.guests):
            raise ValueError("Duplicate held guest identity")
        return self


def assess(observed, commitments, candidate):
    """Assess one physical node and its shared storage against all active holds.

    Storage commitments from other physical nodes still consume shared pool/raw
    space. Without that other node's fresh original-generation proof, they are
    charged in full. Unknown or changed materialization never releases capacity.
    """
    active = [row for row in commitments if row.phase == "held"]
    if candidate is not None:
        if (candidate.physical_node_id, candidate.hardware_binding) != (
            observed.node.physical_node_id,
            observed.node.hardware_binding,
        ):
            raise ValueError("Candidate lacks its original observed physical node")
        if candidate.phase != "held" or any(
            row.id == candidate.id for row in commitments
        ):
            raise ValueError("Candidate must be a new active commitment")
        active.append(candidate)
    if len({row.id for row in active}) != len(active) or len(
        {row.allocation_id for row in active}
    ) != len(active):
        raise ValueError("Duplicate canonical commitment or allocation")
    owned = set()
    original_volumes = set()
    for row in active:
        for guest in row.guests:
            key = (row.physical_node_id, guest.vm_id)
            if key in owned:
                raise ValueError("A physical guest has two active commitments")
            owned.add(key)
            if guest.materialization:
                for volume in guest.materialization.volumes:
                    volume_key = (volume.pool_id, volume.namespace, volume.volume)
                    if volume_key in original_volumes:
                        raise ValueError("An original volume has two active owners")
                    original_volumes.add(volume_key)
    resources = observed.resources
    native = {guest.vm_id: guest for guest in resources.guests}
    pools, clusters, volumes = {}, {}, {}
    for backing in resources.storage:
        previous = pools.get(backing.pool_id)
        if previous and (previous["cluster_id"], previous["replicas"]) != (
            backing.cluster_id,
            backing.replicas,
        ):
            raise ValueError("Shared pool has conflicting cluster or replication facts")
        if previous is None:
            pools[backing.pool_id] = {
                "cluster_id": backing.cluster_id,
                "replicas": backing.replicas,
                "available_bytes": backing.logical_available_bytes,
                "potential_bytes": 0,
                "held_bytes": 0,
            }
        else:
            previous["available_bytes"] = min(
                previous["available_bytes"], backing.logical_available_bytes
            )
        raw = clusters.setdefault(
            backing.cluster_id,
            {
                "available_bytes": backing.cluster_raw_available_bytes,
                "potential_bytes": 0,
                "held_bytes": 0,
            },
        )
        raw["available_bytes"] = min(
            raw["available_bytes"], backing.cluster_raw_available_bytes
        )
        for volume in backing.volumes:
            key = (backing.pool_id, backing.namespace, volume.volume)
            volumes[key] = max(volumes.get(key, 0), volume.size_bytes)
    for (pool_id, _, _), size in volumes.items():
        pool = pools[pool_id]
        pool["potential_bytes"] += size
        clusters[pool["cluster_id"]]["potential_bytes"] += size * pool["replicas"]
    memory = sum(guest.memory_mib for guest in resources.guests)
    cpus = sum(guest.vcpus for guest in resources.guests)
    for hold in active:
        local = hold.physical_node_id == observed.node.physical_node_id
        if local and hold.hardware_binding != observed.node.hardware_binding:
            raise ValueError("Original physical hardware binding changed")
        for guest in hold.guests:
            actual = native.get(guest.vm_id) if local else None
            receipt = guest.materialization
            matched = bool(
                actual
                and not actual.template
                and receipt
                and actual.vmgenid == receipt.vmgenid
                and actual.configuration_revision == receipt.configuration_revision
            )
            if local:
                memory += max(
                    0, guest.memory_mib - (actual.memory_mib if matched else 0)
                )
                cpus += max(0, guest.vcpus - (actual.vcpus if matched else 0))
            original = (
                {
                    (volume.pool_id, volume.namespace, volume.volume): volume.size_bytes
                    for volume in receipt.volumes
                }
                if matched
                else {}
            )
            for storage in guest.storage:
                pool = pools.get(storage.pool_id)
                cluster = clusters.get(storage.cluster_id)
                if pool and (pool["cluster_id"], pool["replicas"]) != (
                    storage.cluster_id,
                    storage.replicas,
                ):
                    raise ValueError(
                        "Held pool backing no longer matches observed native facts"
                    )
                # A verified receipt can discount only exact original volumes
                # whose fresh native size still matches that recorded receipt.
                materialized = sum(
                    size
                    for key, size in original.items()
                    if key[:2] == (storage.pool_id, storage.namespace)
                    and volumes.get(key) == size
                )
                additional = max(0, storage.logical_bytes - materialized)
                if pool:
                    pool["held_bytes"] += additional
                if cluster:
                    cluster["held_bytes"] += additional * storage.replicas
                if candidate is hold and (not local or pool is None or cluster is None):
                    raise ValueError(
                        "Candidate lacks its original node or approved storage backing"
                    )
    memory_headroom = resources.memory_total_mib - resources.memory_reserve_mib - memory
    cpu_headroom = resources.max_vcpus - cpus
    reasons = []
    if memory_headroom < 0:
        reasons.append("MEMORY_COMMITTED")
    if cpu_headroom < 0:
        reasons.append("CPU_BUDGET_COMMITTED")
    for pool in pools.values():
        pool["logical_headroom_bytes"] = (
            pool["available_bytes"] - pool["potential_bytes"] - pool["held_bytes"]
        )
    for cluster in clusters.values():
        cluster["raw_headroom_bytes"] = (
            cluster["available_bytes"]
            - cluster["potential_bytes"]
            - cluster["held_bytes"]
        )
    if any(pool["logical_headroom_bytes"] < 0 for pool in pools.values()) or any(
        cluster["raw_headroom_bytes"] < 0 for cluster in clusters.values()
    ):
        reasons.append("SHARED_STORAGE_COMMITTED")
    return {
        "admissible": not reasons,
        "memory_headroom_mib": memory_headroom,
        "vcpu_headroom": cpu_headroom,
        "pools": pools,
        "clusters": clusters,
        "blocking_codes": reasons,
        "allocation_granted": False,
    }
