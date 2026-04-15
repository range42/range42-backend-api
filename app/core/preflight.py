"""Preflight checks — structured, enumerated, callable outside route bodies.

Each check returns a PreflightCheck with result ∈ {pass, warn, block}.
Aggregate result is 'block' if any check blocks, else 'warn' if any warns,
else 'pass'.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from app.core.vmid_guard import assert_vmid_safe, VmidProtectedError


@dataclass
class PreflightCheck:
    check: str
    result: str  # pass | warn | block
    detail: str = ""
    field_path: str | None = None


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
