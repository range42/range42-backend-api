"""Preflight checks — structured, enumerated, callable outside route bodies.

Each check returns a PreflightCheck with result ∈ {pass, warn, block}.
Aggregate result is 'block' if any check blocks, else 'warn' if any warns,
else 'pass'.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import httpx

from app.core.vmid_guard import assert_vmid_safe, VmidProtectedError


@dataclass
class PreflightCheck:
    check: str
    result: str  # pass | warn | block
    detail: str = ""
    field_path: str | None = None
    code: str | None = None


@dataclass
class PreflightReport:
    checks: list[PreflightCheck] = field(default_factory=list)

    @property
    def result(self) -> str:
        if any(c.result == "block" for c in self.checks):
            return "block"
        if any(c.result == "warn" for c in self.checks):
            return "warn"
        return "pass"


def check_vmids(requested: list[int], *, host_overrides: list[list[int]] | None) -> PreflightCheck:
    for v in requested:
        try:
            assert_vmid_safe(v, host_overrides=host_overrides)
        except VmidProtectedError as e:
            return PreflightCheck(
                check="vmid_collision",
                result="block",
                detail=f"VMID {e.vmid} in protected range",
                field_path="vm.vm_id",
            )
    if len(set(requested)) != len(requested):
        return PreflightCheck(
            check="vmid_collision",
            result="block",
            detail="duplicate VMIDs in plan",
            field_path="vm.vm_id",
        )
    return PreflightCheck(check="vmid_collision", result="pass")


def check_resource_budget(*, total_ram_mb_required: int,
                          host_total_ram_mb: int) -> PreflightCheck:
    if host_total_ram_mb <= 0:
        return PreflightCheck(
            check="resource_budget",
            result="block",
            detail="Host capacity unknown",
            field_path="target_host_id",
        )
    ratio = total_ram_mb_required / host_total_ram_mb
    if ratio > 1.10:
        return PreflightCheck(
            check="resource_budget",
            result="block",
            detail=f"{total_ram_mb_required}MB required > {host_total_ram_mb}MB capacity",
            field_path="team_count",
        )
    if ratio > 0.90:
        return PreflightCheck(
            check="resource_budget",
            result="warn",
            detail=f"{int(ratio * 100)}% of host capacity",
            field_path="team_count",
        )
    return PreflightCheck(check="resource_budget", result="pass")


async def check_proxmox_api_status(api_url: str, token_ref: str) -> PreflightCheck:
    try:
        async with httpx.AsyncClient(verify=False, timeout=5) as cli:
            r = await cli.get(
                f"{api_url}/api2/json/nodes",
                headers={"Authorization": f"PVEAPIToken={token_ref}"},
            )
    except httpx.HTTPError as e:
        return PreflightCheck(
            check="proxmox_api",
            result="block",
            detail=f"Proxmox API unreachable: {e}",
            field_path="target_host_id",
        )
    if r.status_code in (401, 403):
        return PreflightCheck(
            check="proxmox_api",
            result="block",
            detail=f"Proxmox API auth failed ({r.status_code})",
            field_path="target_host_id",
        )
    if r.status_code != 200:
        return PreflightCheck(
            check="proxmox_api",
            result="block",
            detail=f"Proxmox API returned {r.status_code}",
            field_path="target_host_id",
        )
    return PreflightCheck(check="proxmox_api", result="pass")


async def check_sdn_bridge(api_url: str, token_ref: str,
                           bridge: str) -> PreflightCheck:
    try:
        async with httpx.AsyncClient(verify=False, timeout=5) as cli:
            r = await cli.get(
                f"{api_url}/api2/json/cluster/sdn/vnets",
                headers={"Authorization": f"PVEAPIToken={token_ref}"},
            )
    except httpx.HTTPError as e:
        return PreflightCheck(
            check="sdn_bridge",
            result="warn",
            detail=f"SDN vnets unreachable: {e}",
        )
    if r.status_code != 200:
        return PreflightCheck(
            check="sdn_bridge",
            result="warn",
            detail=f"SDN vnets unreadable ({r.status_code})",
        )
    vnets = (r.json().get("data") or [])
    if any(v.get("vnet") == bridge for v in vnets):
        return PreflightCheck(check="sdn_bridge", result="pass")
    return PreflightCheck(
        check="sdn_bridge",
        result="warn",
        detail=f"Bridge {bridge} not in SDN vnets",
        field_path="default_bridge",
    )


def check_secret_completeness(env: list[dict], provided: dict[str, str]) -> PreflightCheck:
    missing = [
        e["name"]
        for e in env
        if e.get("secret") and e.get("required", True) and e["name"] not in (provided or {})
    ]
    if missing:
        return PreflightCheck(
            check="secret_completeness",
            result="block",
            detail=f"Missing required secrets: {','.join(missing)}",
            field_path="secrets",
        )
    return PreflightCheck(check="secret_completeness", result="pass")


async def check_docker_image_pull(images: list[str]) -> PreflightCheck:
    # Best-effort HEAD to Docker Hub manifests; skip pull here (runner does it).
    failed: list[str] = []
    async with httpx.AsyncClient(timeout=8) as cli:
        for image in images:
            if "/" not in image:
                image = "library/" + image
            name, _, tag = image.partition(":")
            tag = tag or "latest"
            try:
                r = await cli.head(f"https://registry-1.docker.io/v2/{name}/manifests/{tag}")
            except httpx.HTTPError:
                failed.append(image)
                continue
            if r.status_code >= 500:
                failed.append(image)
    if failed:
        return PreflightCheck(
            check="docker_image_pull",
            result="block",
            detail=f"Registry error for: {', '.join(failed)}",
            field_path="attachments",
        )
    return PreflightCheck(check="docker_image_pull", result="pass")


async def check_git_reachable(repo_url: str) -> PreflightCheck:
    """HEAD to the repo URL — catches basic reachability/404/auth issues."""
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True) as cli:
            r = await cli.head(repo_url)
    except httpx.HTTPError as e:
        return PreflightCheck(
            check="git_reachable",
            result="block",
            detail=f"Git remote unreachable: {e}",
            field_path="project.source",
        )
    if r.status_code in (401, 403):
        return PreflightCheck(
            check="git_reachable",
            result="block",
            detail=f"Git remote auth failed ({r.status_code})",
            field_path="project.source",
        )
    if r.status_code >= 500:
        return PreflightCheck(
            check="git_reachable",
            result="warn",
            detail=f"Git remote returned {r.status_code}",
            field_path="project.source",
        )
    return PreflightCheck(check="git_reachable", result="pass")


async def check_vmid_safety_for_topology(
    topology: dict,
    team_count: int,
    host_overrides: list[list[int]] | None,
) -> PreflightCheck:
    """Wrap ``check_vmids()`` with topology-aware VMID expansion.

    Mirrors the universal playbook's ``r42_vmid_for_node`` filter:
    - shared scope: ``(vmid_base or template_vmid) + seq`` (seq within shared list)
    - per_team scope: for ``team_id in 1..team_count``,
      ``(vmid_base or template_vmid) + (team_id * vms_per_team) + seq``
      (seq within per-team list, ``vms_per_team`` = total per-team VM count).

    Async for call-site symmetry with the other ``check_*`` async functions
    even though no I/O is performed — just expansion + sync ``check_vmids``.
    """
    nodes = topology.get("nodes") or []
    vm_kinds = ("vm", "lxc")

    shared_vms = [
        n for n in nodes
        if n.get("kind") in vm_kinds
        and (n.get("replication") or {}).get("scope") == "shared"
    ]
    per_team_vms = [
        n for n in nodes
        if n.get("kind") in vm_kinds
        and (n.get("replication") or {}).get("scope") == "per_team"
    ]

    vmids: list[int] = []

    # Shared VMs use vmid_base + seq
    for seq, n in enumerate(shared_vms):
        base = n.get("vmid_base", n.get("template_vmid", 0))
        vmids.append(int(base) + seq)

    # Per-team: vmid_base + (team_id * vms_per_team) + seq
    vms_per_team = len(per_team_vms)
    for team_id in range(1, team_count + 1):
        for seq, n in enumerate(per_team_vms):
            base = n.get("vmid_base", n.get("template_vmid", 0))
            vmids.append(int(base) + (team_id * vms_per_team) + seq)

    return check_vmids(vmids, host_overrides=host_overrides)


async def check_topology_assets(
    project_dir: Path,
    catalog_dir: Path | None,
    topology: dict,
    *,
    registered_source_base_urls: set[str] | None = None,
) -> list[PreflightCheck]:
    """Verify every attachment ref in the topology resolves to a real artifact.

    For each node's attachments[]:
    - kind=file_upload: project_dir / source.path must exist
    - kind=inline_yaml: source.content must be non-empty (whitespace-trimmed)
    - kind=external_git: source.url's host must be in registered_source_base_urls
    - kind=catalog_role / catalog_container: catalog_dir / source.ref must exist
      (warns if catalog_dir is None — caller hasn't checked out catalog yet)

    Returns a list with one entry per failure, or a single 'pass' if all resolve.
    """
    checks: list[PreflightCheck] = []
    registered = registered_source_base_urls or set()

    for node in (topology.get("nodes") or []):
        for idx, att in enumerate(node.get("attachments") or []):
            source = (att.get("source") or {})
            kind = source.get("kind")
            node_path = f"nodes[{node.get('id')}].attachments[{idx}]"

            if kind == "file_upload":
                rel_path = source.get("path") or ""
                if not rel_path or not (project_dir / rel_path).is_file():
                    checks.append(PreflightCheck(
                        check="topology_assets",
                        result="block",
                        detail=f"file_upload path '{rel_path}' not found in project repo",
                        field_path=node_path,
                        code="MISSING_ASSET",
                    ))
            elif kind == "inline_yaml":
                content = source.get("content") or ""
                if not content.strip():
                    checks.append(PreflightCheck(
                        check="topology_assets",
                        result="block",
                        detail="inline_yaml content is empty",
                        field_path=node_path,
                        code="MISSING_ASSET",
                    ))
            elif kind == "external_git":
                url = source.get("url") or ""
                parsed = urlparse(url)
                base = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
                if not base or base not in registered:
                    checks.append(PreflightCheck(
                        check="topology_assets",
                        result="block",
                        detail=(
                            f"external_git URL host {base or url} is not a registered Source "
                            f"(allowed: {sorted(registered)})"
                        ),
                        field_path=node_path,
                        code="EXTERNAL_GIT_NOT_REGISTERED",
                    ))
            elif kind in ("catalog_role", "catalog_container"):
                ref = source.get("ref") or ""
                if catalog_dir is None:
                    checks.append(PreflightCheck(
                        check="topology_assets",
                        result="warn",
                        detail=f"catalog_dir not provided; cannot verify {kind} ref '{ref}'",
                        field_path=node_path,
                        code="CATALOG_NOT_RESOLVED",
                    ))
                elif not ref or not (catalog_dir / ref).exists():
                    checks.append(PreflightCheck(
                        check="topology_assets",
                        result="block",
                        detail=f"{kind} ref '{ref}' not found in catalog",
                        field_path=node_path,
                        code="MISSING_ASSET",
                    ))

    if not checks:
        checks.append(PreflightCheck(
            check="topology_assets",
            result="pass",
            detail="all attachment refs resolved",
        ))
    return checks


def check_topology_node_role(topology: dict) -> list[PreflightCheck]:
    """Every VM/LXC topology node must declare a non-empty 'role'."""
    checks: list[PreflightCheck] = []
    for node in (topology.get("nodes") or []):
        if node.get("kind") not in ("vm", "lxc"):
            continue
        if not node.get("role"):
            checks.append(PreflightCheck(
                check="topology_node_role",
                result="block",
                detail=f"VM/LXC node {node.get('id')} missing 'role'",
                field_path=f"nodes[{node.get('id')}]",
                code="TOPOLOGY_NODE_MISSING_ROLE",
            ))
    if not checks:
        checks.append(PreflightCheck(
            check="topology_node_role",
            result="pass",
            detail="all VM/LXC nodes have role",
        ))
    return checks


# ---------------------------------------------------------------------------
# Declarative preflight_checks[] dispatcher (spec §5.5.4)
#
# Topologies MAY declare ``preflight_checks: [...]`` listing named checks the
# backend should run.  Names map to the existing ``check_*`` callables here.
# ---------------------------------------------------------------------------

_DECLARATIVE_CHECKS: dict[str, Callable] = {
    "proxmox.connectivity": check_proxmox_api_status,
    "vmid.safety": check_vmid_safety_for_topology,
    "topology.assets": check_topology_assets,
    "topology.node_role": check_topology_node_role,
    "secrets.completeness": check_secret_completeness,
    "git.reachable": check_git_reachable,
    "docker.images": check_docker_image_pull,
    "sdn.bridge": check_sdn_bridge,
    "resource.budget": check_resource_budget,
}


async def run_declarative_checks(
    names: list[str],
    *,
    context: dict,
) -> list[PreflightCheck]:
    """Run named checks from the declarative preflight_checks[] list.

    Unknown names → warn with code=UNKNOWN_PREFLIGHT_CHECK; do not raise.

    The dispatcher introspects each callable's signature via
    ``inspect.signature`` and passes only the kwargs it needs from
    ``context``.  Missing kwargs are simply not passed (the callable will
    raise ``TypeError`` if a required argument is absent — caught and
    surfaced as a ``DECLARATIVE_CHECK_RAISED`` warn).
    """
    out: list[PreflightCheck] = []

    for name in names:
        fn = _DECLARATIVE_CHECKS.get(name)
        if fn is None:
            out.append(PreflightCheck(
                check="declarative",
                result="warn",
                code="UNKNOWN_PREFLIGHT_CHECK",
                detail=f"unknown declarative check: {name}",
            ))
            continue

        try:
            sig = inspect.signature(fn)
            # Build kwargs from context for parameters that exist in `sig`
            # and are present in `context`.  Skip *args/**kwargs.
            kwargs = {
                pname: context[pname]
                for pname, p in sig.parameters.items()
                if p.kind not in (
                    inspect.Parameter.VAR_POSITIONAL,
                    inspect.Parameter.VAR_KEYWORD,
                )
                and pname in context
            }

            if inspect.iscoroutinefunction(fn):
                result = await fn(**kwargs)
            else:
                result = fn(**kwargs)
        except Exception as e:
            out.append(PreflightCheck(
                check="declarative",
                result="warn",
                code="DECLARATIVE_CHECK_RAISED",
                detail=f"check '{name}' raised {type(e).__name__}: {e}",
            ))
            continue

        if isinstance(result, list):
            out.extend(result)
        elif isinstance(result, PreflightCheck):
            out.append(result)
        else:
            # Defensive: callable returned something unexpected.
            out.append(PreflightCheck(
                check="declarative",
                result="warn",
                code="DECLARATIVE_CHECK_RAISED",
                detail=(
                    f"check '{name}' returned unexpected type "
                    f"{type(result).__name__}"
                ),
            ))

    return out
