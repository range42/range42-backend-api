"""Canonical reusable template preparation, separate from participant runs."""

from typing import Literal
from dataclasses import dataclass
import re

from pydantic import Field, model_validator

from app.core.errors import Range42Error
from app.core.provisioning_models import StrictModel
from app.core.provisioning_profiles import Role, TemplateId, resource_profile
from pydantic import TypeAdapter


class FoundationDescriptor(StrictModel):
    version: int = Field(strict=True, ge=1, le=1)
    kind: Literal["native-template-preparation", "native-workshop-foundation"]
    title: str = Field(min_length=1, max_length=120)


class FoundationSpec(StrictModel):
    namespace: str = Field(pattern=r"^[a-z][a-z0-9-]{0,19}$")
    source_node: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    proxmox_source: int = Field(ge=1000, le=999999999)
    deployer_source: int = Field(ge=1000, le=999999999)

    @model_validator(mode="after")
    def distinct_sources(self):
        if self.proxmox_source == self.deployer_source:
            raise ValueError("Foundation source templates must be distinct")
        return self


def refused(
    message="Review the original bounded environment and template preparation plan.",
):
    return Range42Error(status=409, code="FOUNDATION_REVIEW_REQUIRED", message=message)


def _compile_legacy(spec: FoundationSpec, context, *, run_id: str):
    from app.core.workshop_profiles import installation_profile
    profile = installation_profile(context)
    if (
        profile is None
        or context.registration_issue
        or not re.fullmatch(r"[a-f0-9]{16}", run_id)
        or not context.target_host_id
        or not context.target_node
        or not context.target_api_url
    ):
        raise refused()
    outputs = [profile.proxmox_template, profile.deployer_template]
    if (
        len(set(outputs)) != 2
        or any(vmid in outputs for vmid in (spec.proxmox_source, spec.deployer_source))
        or any(vmid < 1000 for vmid in outputs)
    ):
        raise refused(
            "Source templates must be separate from this environment’s reusable copies."
        )
    templates = []
    for role, source, destination, memory, cores, disk in [
        ("proxmox", spec.proxmox_source, profile.proxmox_template, 16384, 4, 160),
        ("deployer", spec.deployer_source, profile.deployer_template, 4096, 2, 32),
    ]:
        templates.append(
            {
                "role": role,
                "source_vm_id": source,
                "vm_id": destination,
                "vm_name": f"{spec.namespace}-{role}-template",
                "owner": f"range42-foundation:{run_id}:{role}",
                "memory_mib": memory,
                "cores": cores,
                "disk_gib": disk,
                "logical_bytes": disk * 2**30 + 4 * 2**20,
                "storage": profile.storage,
            }
        )
    return {
        "version": 1,
        "kind": "native-workshop-foundation",
        "run_id": run_id,
        "environment_id": context.id,
        "namespace": spec.namespace,
        "source_node": spec.source_node,
        "target_node": context.target_node,
        "storage": profile.storage,
        "templates": templates,
        "assignments": [
            {
                "node_id": f"foundation-{row['role']}",
                "vm_id": row["vm_id"],
                "nics": [],
                "run_namespace": "foundation:" + spec.namespace,
            }
            for row in templates
        ],
        "capacity": {
            "memory_mib": 20480,
            "vcpus": 6,
            "logical_bytes": sum(row["logical_bytes"] for row in templates),
        },
    }


class TemplatePreparationSpec(StrictModel):
    namespace: str = Field(pattern=r"^[a-z][a-z0-9-]{0,19}$")
    source_node: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    sources: dict[Role, TemplateId] = Field(min_length=1, max_length=16)


PreparationSpec = FoundationSpec | TemplatePreparationSpec


def parse_spec(value):
    return TypeAdapter(PreparationSpec).validate_python(value)


def compile_templates(spec: TemplatePreparationSpec, context, *, run_id: str):
    profile = resource_profile(context)
    if (
        profile is None
        or context.registration_issue
        or not re.fullmatch(r"[a-f0-9]{16}", run_id)
        or not context.target_host_id
        or not context.target_node
        or not context.target_api_url
    ):
        raise refused()
    if set(spec.sources) != set(profile.templates):
        raise refused("Select the complete approved template set for this environment.")
    outputs = {row.vm_id for row in profile.templates.values()}
    if len(set(spec.sources.values())) != len(spec.sources) or outputs.intersection(
        spec.sources.values()
    ):
        raise refused("Sources must be distinct and separate from all reusable copies.")
    templates = []
    for role in sorted(spec.sources):
        target = profile.templates[role]
        disk = sum(target.disks_gib)
        templates.append(
            {
                "role": role,
                "source_vm_id": spec.sources[role],
                "vm_id": target.vm_id,
                "vm_name": f"{spec.namespace}-{role}-template",
                "owner": f"range42-foundation:{run_id}:{role}",
                "memory_mib": target.memory_mib,
                "cores": target.cores,
                "disk_gib": disk,
                "disks_gib": list(target.disks_gib),
                "logical_bytes": disk * 2**30 + 4 * 2**20,
                "storage": profile.storage,
            }
        )
    return {
        "version": 1,
        "kind": "native-template-preparation",
        "run_id": run_id,
        "environment_id": context.id,
        "namespace": spec.namespace,
        "source_node": spec.source_node,
        "target_node": context.target_node,
        "storage": profile.storage,
        "templates": templates,
        "assignments": [
            {
                "node_id": "foundation-" + row["role"],
                "vm_id": row["vm_id"],
                "nics": [],
                "run_namespace": "foundation:" + spec.namespace,
            }
            for row in templates
        ],
        "capacity": {
            "memory_mib": sum(row["memory_mib"] for row in templates),
            "vcpus": sum(row["cores"] for row in templates),
            "logical_bytes": sum(row["logical_bytes"] for row in templates),
        },
    }


def compile_foundation(spec, context, *, run_id):
    return (
        compile_templates(spec, context, run_id=run_id)
        if isinstance(spec, TemplatePreparationSpec)
        else _compile_legacy(spec, context, run_id=run_id)
    )


def foundation_topology(plan):
    return {
        "vms": [{**row, "role": "prepared-template"} for row in plan["templates"]],
        "templates": [
            {
                "vm_id": row["source_vm_id"],
                "vm_name": f"{row['role']} source",
                "role": "template",
                "readonly": True,
            }
            for row in plan["templates"]
        ],
        "networks": [],
    }


def resolve_native_foundation(native, descriptor, context, *, run_id):
    declared, selected = descriptor.get("preparation"), native.get("foundation")
    if declared is None and selected is None:
        return None
    if (
        not declared
        or selected is None
        or descriptor["declared_vmids"]
        or native.get("workshop")
        or native.get("parameters")
        or native.get("features")
    ):
        raise refused(
            "Choose the explicit foundation scenario and its managed preparation inputs."
        )
    parsed = FoundationDescriptor.model_validate(declared)
    if (parsed.kind == "native-template-preparation") != isinstance(
        parse_spec(selected), TemplatePreparationSpec
    ):
        raise refused("The preparation inputs must match the selected catalog recipe.")
    plan = compile_foundation(parse_spec(selected), context, run_id=run_id)
    if native.get("foundation_plan") is not None and native["foundation_plan"] != plan:
        raise refused(
            "The retained foundation plan changed. Preserve its original resource ownership."
        )
    return plan


def _host_revision(host):
    from app.core.physical_nodes import digest, target_revision

    return digest([target_revision(host), host.protected_vmids_override_json])


@dataclass(frozen=True)
class FoundationAllocationAuthority:
    """Internal authority derived from the selected profile, never an HTTP field."""

    host_revision: str
    identities: tuple[tuple[str, int], ...]

    def validate_assignments(self, assignments, host):
        if (
            self.host_revision != _host_revision(host)
            or any(row.get("nics") != [] for row in assignments)
            or tuple(
                sorted((row.get("node_id"), row.get("vm_id")) for row in assignments)
            )
            != self.identities
        ):
            raise refused(
                "The foundation allocation or original target changed. Review its exact template copies."
            )

    def validate_request(self, request, host):
        self.validate_assignments([row.model_dump() for row in request.vms], host)
        ids = [vmid for _, vmid in self.identities]
        if (
            request.networks
            or request.vmid_start != min(ids)
            or request.vmid_end != max(ids)
        ):
            raise refused(
                "Foundation allocation reserves only its exact networkless template copies."
            )

    def permits_template(self, vmid):
        # Only the conventional template band has this narrow default-policy
        # exception. Explicit host exclusions and all other protected bands stay.
        return 9000 <= vmid <= 9999 and any(
            identity == vmid for _, identity in self.identities
        )


def allocation_authority(spec, context, host, *, run_id):
    if (host.id, host.node_name, host.api_url) != (
        context.target_host_id,
        context.target_node,
        context.target_api_url,
    ):
        raise refused()
    plan = compile_foundation(spec, context, run_id=run_id)
    return FoundationAllocationAuthority(
        _host_revision(host),
        tuple(sorted((row["node_id"], row["vm_id"]) for row in plan["assignments"])),
    )


def allocation_check(spec, context, host, *, run_id, vmids):
    from app.core.allocation_reservations import _protected, identity_protected
    from app.core.preflight import PreflightCheck

    authority = allocation_authority(spec, context, host, run_id=run_id)
    if sorted(vmids) != sorted(vmid for _, vmid in authority.identities) or any(
        identity_protected(vmid, _protected(host), authority) for vmid in vmids
    ):
        raise refused(
            "The exact original template copies are required and explicit target exclusions remain protected."
        )
    return PreflightCheck(
        check="vmid_collision",
        result="pass",
        detail="Exact foundation template copies retain their separate Core allocation.",
    )
