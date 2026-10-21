"""Optional environment networking retains Core ownership and its original hold."""

from ipaddress import IPv4Interface, IPv4Network
import json
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.core import capacity_ledger as ledger
from app.core.physical_capacity import Digest, StrictObservation
from app.core.physical_nodes import NodeAttestation, digest
from app.core.workshop_profiles import WorkshopNetwork
from sqlalchemy import select, text

from app.core import (
    environment_network_transport as transport,
    foundation_execution,
    native_capacity,
)
from app.core.capacity_models import CapacityHold
from app.core.environment_networks import compile_plan
from app.core.models import Deployment
from app.core.native_environment_models import NativeEnvironment


def private(network):
    return any(
        network.subnet_of(IPv4Network(value))
        for value in ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
    )


class Management(StrictObservation):
    bridge: str = Field(pattern=r"^vmbr[1-9][0-9]{2,4}$")
    subnet: str = Field(max_length=18)
    host_address: str = Field(max_length=18)


class Uplink(StrictObservation):
    interface: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,14}$")
    address: str = Field(max_length=18)
    gateway: str = Field(max_length=15)


class NetworkPolicy(StrictObservation):
    version: int = Field(ge=1, le=1)
    node_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9.-]{0,127}$")
    installation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    networks: list[WorkshopNetwork] = Field(min_length=1, max_length=64)
    management: Management
    uplink: Uplink
    routing_table: int = Field(ge=40000, le=49999)
    routing_priority: int = Field(ge=4000, le=4998)
    protected_public_cidrs: list[str] = Field(max_length=64)

    @model_validator(mode="after")
    def bounds(self):
        management = IPv4Network(self.management.subnet, strict=True)
        if (
            management.prefixlen != 29
            or not private(management)
            or self.management.host_address != f"{management[1]}/29"
        ):
            raise ValueError("Original private management network required")
        names = [row.bridge for row in self.networks] + [self.management.bridge]
        pools = [IPv4Network(row.subnet) for row in self.networks] + [management]
        if (
            len(set(names)) != len(names)
            or any(not 500 <= int(name[4:]) <= 65535 for name in names)
            or any(
                left.overlaps(right)
                for index, left in enumerate(pools)
                for right in pools[index + 1 :]
            )
            or self.networks
            != sorted(self.networks, key=lambda row: int(row.bridge[4:]))
        ):
            raise ValueError("Exact disjoint canonical network pool required")
        uplink = IPv4Interface(self.uplink.address)
        if (
            uplink.network.prefixlen != 30
            or not private(uplink.network)
            or str(uplink) != self.uplink.address
            or (
                str(uplink.ip) not in {str(uplink.network[1]), str(uplink.network[2])}
                or self.uplink.gateway
                not in {str(uplink.network[1]), str(uplink.network[2])}
                - {str(uplink.ip)}
                or self.uplink.interface in names
                or any(uplink.network.overlaps(pool) for pool in pools)
            )
        ):
            raise ValueError("Original independent transit peer required")
        protected = [
            IPv4Network(value, strict=True) for value in self.protected_public_cidrs
        ]
        if (
            self.protected_public_cidrs != sorted(str(value) for value in protected)
            or any(not value.is_global or value.prefixlen < 8 for value in protected)
            or any(
                left.overlaps(right)
                for index, left in enumerate(protected)
                for right in protected[index + 1 :]
            )
        ):
            raise ValueError("Exact disjoint protected site prefixes required")
        return self


class ClosedExecution(StrictObservation):
    execution_ready: Literal[False]

    @field_validator("execution_ready", mode="before")
    @classmethod
    def no_general_execution(cls, value):
        if value is not False:
            raise ValueError("Network preparation cannot grant general execution")
        return value


class NetworkProfile(ClosedExecution):
    protocol: Literal["range42-environment-network-profile-v1"]
    policy: NetworkPolicy
    policy_revision: Digest
    node: NodeAttestation
    supported_operations: list[str] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def revision(self):
        if (
            self.policy_revision != digest(self.policy.model_dump())
            or self.supported_operations != ["profile", "prepare", "status"]
            or (
                self.policy.node_name != self.node.node_name
                or self.policy.installation_id != self.node.installation_id
            )
        ):
            raise ValueError("Original independent network profile required")
        return self


class Owner(StrictObservation):
    protocol: Literal["range42-environment-network-owner-v1"]
    physical_node_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    hardware_binding: Digest
    installation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    node_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9.-]{0,127}$")


class NetworkProof(StrictObservation):
    request_digest: Digest
    owner: Owner
    interfaces_digest: Digest
    firewall_digest: Digest
    routes_digest: Digest


class NetworkReceipt(ClosedExecution):
    protocol: Literal["range42-environment-network-receipt-v1"]
    request_digest: Digest
    policy_revision: Digest
    environment_id: str = Field(pattern=r"^env-[0-9a-f]{24}$")
    node: NodeAttestation
    status: Literal["absent", "preparing", "ready"]
    network_ready: bool
    proof: NetworkProof | None

    @model_validator(mode="after")
    def ready(self):
        if (
            self.network_ready != (self.status == "ready")
            or (self.proof is not None) != self.network_ready
        ):
            raise ValueError("Readiness must match original root proof")
        return self


def verified_receipt(request, value):
    try:
        result = NetworkReceipt.model_validate(value)
        original = request["foundation_request"]["capacity_request"]
        commitment = original["commitment"]
        if (
            result.request_digest != digest(request)
            or result.policy_revision != request["policy_revision"]
            or result.environment_id != request["plan"]["environment_id"]
            or (
                result.node.physical_node_id != commitment["physical_node_id"]
                or result.node.hardware_binding != commitment["hardware_binding"]
                or result.node.configuration_revision
                != original["root_policy_revision"]
                or result.node.node_name != request["plan"]["node"]
                or result.node.installation_id != original["installation_id"]
            )
        ):
            raise ValueError("Original network request differs")
        if result.proof and (
            result.proof.request_digest != result.request_digest
            or any(
                getattr(result.proof.owner, field) != getattr(result.node, field)
                for field in [
                    "physical_node_id",
                    "hardware_binding",
                    "installation_id",
                    "node_name",
                ]
            )
        ):
            raise ValueError("Original network owner proof differs")
        return result
    except (ValueError, TypeError, KeyError):
        raise ledger.refused(
            "ENVIRONMENT_NETWORK_RECEIPT_MISMATCH",
            "The physical-node reply does not prove this original environment network request. Preserve its ownership and reconcile it.",
        ) from None


def verified_profile(value, configuration, original_node, plan):
    try:
        profile = NetworkProfile.model_validate(value)
        foundation_execution.verify_node(original_node, profile.node)
        if (
            configuration.installation_id != profile.policy.installation_id
            or configuration.node_name != profile.policy.node_name
            or (
                plan["node"] != profile.policy.node_name
                or plan["management"] != profile.policy.management.model_dump()
                or any(
                    row not in [item.model_dump() for item in profile.policy.networks]
                    for row in plan["networks"]
                )
            )
        ):
            raise ValueError("Original registered network scope differs")
        return profile
    except (ValueError, TypeError, KeyError):
        raise ledger.refused(
            "ENVIRONMENT_NETWORK_PROFILE_MISMATCH",
            "The optional network module does not match this registered environment and original physical node.",
        ) from None


def make_request(foundation, plan, profile):
    return {
        "protocol": "range42-environment-network-request-v1",
        "policy_revision": profile.policy_revision,
        "foundation_request": foundation,
        "plan": plan,
    }


async def original(session, deployment_id, *, required=True):
    """Recompile only from the existing registered environment and Core hold."""
    dep = await session.get(Deployment, deployment_id, populate_existing=True)
    if (
        dep is None
        or not dep.native
        or not dep.native.get("prepare_environment_networks")
    ):
        if not required:
            return None
        raise ledger.refused(
            "ENVIRONMENT_NETWORK_NOT_REQUESTED",
            "This foundation did not request optional environment network preparation.",
        )
    row = await session.scalar(
        select(CapacityHold)
        .where(CapacityHold.deployment_id == deployment_id)
        .execution_options(populate_existing=True)
    )
    if row is None or row.preparation_request_json is None:
        raise ledger.refused(
            "ENVIRONMENT_NETWORK_FOUNDATION_REQUIRED",
            "Retain and physically accept this foundation’s original preparation request before reviewing bridges.",
        )
    foundation, host, context = await foundation_execution.original(session, row)
    plan = compile_plan(context)
    environment = await session.get(
        NativeEnvironment, context.id, populate_existing=True
    )
    if environment is None:
        raise ledger.refused()
    configuration = transport.configuration(host.id, host.node_name)
    node = native_capacity.NativeReceipt.model_validate_json(
        row.native_receipt_json
    ).node
    if environment.network_preparation_json is None:
        if not required:
            return foundation, host, context, environment, configuration, node, None
        raise ledger.refused(
            "ENVIRONMENT_NETWORK_REQUEST_REQUIRED",
            "Review and retain the original bridge request in Overview before starting this foundation.",
        )
    try:
        if len(environment.network_preparation_json.encode()) > 65536:
            raise ValueError("Bounded original network journal required")
        stored = json.loads(environment.network_preparation_json)
        if (
            not isinstance(stored, dict)
            or set(stored)
            != {
                "version",
                "deployment_id",
                "transport_revision",
                "profile",
                "request",
                "receipt",
            }
            or type(stored["version"]) is not int
            or stored["version"] != 1
            or (
                stored["deployment_id"] != dep.id
                or stored["transport_revision"] != configuration.revision
            )
        ):
            raise ValueError("Original network transport or ownership changed")
        profile = verified_profile(stored["profile"], configuration, node, plan)
        request = make_request(foundation, plan, profile)
        if stored["request"] != request:
            raise ValueError("Original retained network request changed")
        if stored["receipt"] is not None:
            verified_receipt(request, stored["receipt"])
        return foundation, host, context, environment, configuration, node, stored
    except (ValueError, TypeError, KeyError):
        raise ledger.refused(
            "ENVIRONMENT_NETWORK_ORIGINAL_CHANGED",
            "The original environment network request or service changed. Preserve its ownership and reconcile it before continuing.",
        ) from None


async def assert_dispatch(session, deployment_id):
    dep = await session.get(Deployment, deployment_id, populate_existing=True)
    if dep and dep.native and dep.native.get("prepare_environment_networks"):
        await original(session, deployment_id)


async def journal(factory, deployment_id):
    # No SQLite writer is held across either physical service observation.
    foundation, _ = await foundation_execution.dispatch_inputs(factory, deployment_id)
    async with factory() as session:
        inputs = await original(session, deployment_id, required=False)
        if inputs is None:
            raise ledger.refused(
                "ENVIRONMENT_NETWORK_NOT_REQUESTED",
                "Select optional environment network preparation when creating this foundation.",
            )
        current, _, context, _, configuration, node, stored = inputs
        if current != foundation:
            raise ledger.refused()
        plan = compile_plan(context)
    profile = verified_profile(
        await transport.read(configuration, action="profile"), configuration, node, plan
    )
    request = make_request(foundation, plan, profile)
    async with factory() as session:
        await session.execute(text("PRAGMA synchronous=FULL"))
        await session.execute(text("BEGIN IMMEDIATE"))
        current, _, _, environment, current_configuration, _, existing = await original(
            session, deployment_id, required=False
        )
        if (
            current != foundation
            or current_configuration.revision != configuration.revision
        ):
            raise ledger.refused()
        if existing is not None:
            if (
                existing["request"] != request
                or NetworkProfile.model_validate(existing["profile"]).policy
                != profile.policy
            ):
                raise ledger.refused(
                    "ENVIRONMENT_NETWORK_ORIGINAL_CHANGED",
                    "The reviewed network module changed after the original request was retained.",
                )
            return existing
        bundle = {
            "version": 1,
            "deployment_id": deployment_id,
            "transport_revision": configuration.revision,
            "profile": profile.model_dump(),
            "request": request,
            "receipt": None,
        }
        environment.network_preparation_json = json.dumps(bundle, sort_keys=True)
        await session.commit()
        return bundle


async def dispatch_inputs(factory, deployment_id):
    foundation, _ = await foundation_execution.dispatch_inputs(factory, deployment_id)
    async with factory() as session:
        current, _, context, _, configuration, node, bundle = await original(
            session, deployment_id
        )
        if current != foundation:
            raise ledger.refused()
        profile = verified_profile(
            await transport.read(configuration, action="profile"),
            configuration,
            node,
            compile_plan(context),
        )
        if profile.policy != NetworkProfile.model_validate(bundle["profile"]).policy:
            raise ledger.refused(
                "ENVIRONMENT_NETWORK_ORIGINAL_CHANGED",
                "The original enrolled network policy changed after review.",
            )
    # Reopen the Core record after remote I/O to close stale dispatch races.
    async with factory() as session:
        *_, latest = await original(session, deployment_id)
        if latest != bundle:
            raise ledger.refused()
    return bundle, configuration


async def run_original(configuration, request, *, rpc=None):
    rpc = rpc or transport.read
    profile = NetworkProfile.model_validate(await rpc(configuration, action="profile"))
    if profile.policy_revision != request["policy_revision"] or (
        profile.node.configuration_revision
        != request["foundation_request"]["capacity_request"]["root_policy_revision"]
        or profile.node.physical_node_id
        != request["foundation_request"]["capacity_request"]["commitment"][
            "physical_node_id"
        ]
        or profile.node.hardware_binding
        != request["foundation_request"]["capacity_request"]["commitment"][
            "hardware_binding"
        ]
        or profile.policy.installation_id != configuration.installation_id
        or profile.policy.node_name != configuration.node_name
    ):
        raise ledger.refused(
            "ENVIRONMENT_NETWORK_ORIGINAL_CHANGED",
            "The original network module changed before native dispatch.",
        )
    result = verified_receipt(
        request, await rpc(configuration, action="status", request=request)
    )
    if result.status != "ready":
        verified_receipt(
            request, await rpc(configuration, action="prepare", request=request)
        )
        result = verified_receipt(
            request, await rpc(configuration, action="status", request=request)
        )
    if result.status != "ready":
        raise ledger.refused(
            "ENVIRONMENT_NETWORK_UNRESOLVED",
            "Bridge preparation is unresolved. Preserve its original request and reconcile node status in Overview.",
        )
    return result.model_dump()


async def reconcile(factory, deployment_id):
    bundle, configuration = await dispatch_inputs(factory, deployment_id)
    value = verified_receipt(
        bundle["request"],
        await transport.read(configuration, action="status", request=bundle["request"]),
    )
    async with factory() as session:
        await session.execute(text("PRAGMA synchronous=FULL"))
        await session.execute(text("BEGIN IMMEDIATE"))
        *_, environment, _, node, current = await original(session, deployment_id)
        if current != bundle:
            raise ledger.refused()
        foundation_execution.verify_node(node, value.node)
        if (
            current["receipt"] is not None
            and current["receipt"]["status"] == "ready"
            and value.status != "ready"
        ):
            raise ledger.refused(
                "ENVIRONMENT_NETWORK_ORIGINAL_CHANGED",
                "Previously prepared environment networks lost their original proof. Preserve ownership and resolve the physical drift.",
            )
        updated = {**current, "receipt": value.model_dump()}
        if updated != current:
            environment.network_preparation_json = json.dumps(updated, sort_keys=True)
            await session.commit()
    return value
