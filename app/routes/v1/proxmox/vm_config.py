"""Digest-guarded imported guest edits, using only the selected registry target.

No Ansible/global inventory, disk/network/boot options, or deployment ownership
mutation is exposed. PVE remains the authority for VM-specific privileges.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Annotated, Literal
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, Path
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.allocation_models import DeploymentAllocation
from app.core.config import Settings
from app.core.errors import Range42Error
from app.core.locks import ProvisioningLock
from app.core.proxmox_tls import proxmox_verify
from app.routes.v1.proxmox._helpers import (
    _assert_vmid_safe, _auth_headers, _config_target_digest, _config_task_vmid,
    _get_host, _session,
)
from app.schemas.v1.vm_config import VmConfigReview, VmConfigUpdate, VmConfigUpdateResult, VmConfigValues

router = APIRouter()
VmId = Annotated[int, Path(ge=100, le=999999999)]
FIELDS = ("name", "description", "cores", "memory", "tags")


def _error(code, message, status=409):
    return Range42Error(code=code, message=message, status=status,
                        error="conflict" if status == 409 else "upstream_error")


def _invalid():
    return _error("VM_CONFIG_UNAVAILABLE", "Proxmox did not provide a consistent readable guest configuration.", 502)


def _digest(row, vmid, vmtype, digest):
    return hashlib.sha256(json.dumps([_config_target_digest(row, vmid, vmtype), digest], separators=(",", ":")).encode()).hexdigest()


def _url(row, vmid, vmtype):
    return f"{row.api_url.rstrip('/')}/api2/json/nodes/{quote(row.node_name, safe='')}/{vmtype}/{vmid}/config"


def _values(config, vmtype):
    result = {}
    for field in FIELDS:
        key = "hostname" if field == "name" and vmtype == "lxc" else field
        value = config.get(key, "" if field in ("description", "tags") else None)
        if field in ("cores", "memory"):
            if isinstance(value, str) and re.fullmatch(r"(?:current=)?[0-9]{1,10}" if field == "memory" else r"[0-9]{1,10}", value):
                value = int(value.removeprefix("current="))
            if value is not None and (type(value) is not int or value < 1 or value > 2**53):
                raise _invalid()
        elif value is not None and (not isinstance(value, str) or len(value) > 8192):
            raise _invalid()
        result[field] = value
    return VmConfigValues(**result)


def _writable(config):
    if config.get("template", 0) not in (0, "0", False):
        raise _error("VM_CONFIG_TEMPLATE", "Template configuration is outside imported guest editing.")
    if config.get("lock"):
        raise _error("VM_CONFIG_LOCKED", "The guest is locked by Proxmox. Review it after that operation finishes.")
    description = config.get("description", "")
    if not isinstance(description, str):
        raise _invalid()
    if "range42-deployment:" in description.lower():
        raise _error("VM_CONFIG_MANAGED", "Use the owning deployment to change this guest.")


async def _claims(session, vmid):
    # Allocation VM IDs are installation-wide, including target aliases. A
    # missing/externally removed marker must not bypass committed ownership.
    for claim in (await session.scalars(select(DeploymentAllocation))).all():
        if not isinstance(claim.assignments, list) or any(not isinstance(vm, dict) or type(vm.get("vm_id")) is not int for vm in claim.assignments):
            raise _error("VM_CONFIG_MANAGED", "Cannot establish imported guest ownership from the deployment ledger.")
        if any(vm["vm_id"] == vmid for vm in claim.assignments):
            raise _error("VM_CONFIG_MANAGED", "Use the owning deployment to change this guest.")


async def _read(cli, row, vmid, vmtype):
    configs = []
    for current in (0, 1):
        try:
            response = await cli.get(_url(row, vmid, vmtype), headers=_auth_headers(row), params={"current": current})
        except httpx.RequestError:
            raise _error("VM_CONFIG_UNAVAILABLE", "Proxmox configuration could not be read. No edit was sent.", 502) from None
        if response.status_code in (401, 403):
            raise _error("VM_CONFIG_FORBIDDEN", "Proxmox denied access to this guest configuration.", 403)
        if response.status_code != 200:
            raise _invalid()
        try:
            config = response.json()["data"]
            if not isinstance(config, dict) or not isinstance(config.get("digest"), str) or not re.fullmatch("[a-f0-9]{40}", config["digest"]):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise _invalid() from None
        _writable(config)
        configs.append(config)
    configured, current = configs
    if configured["digest"] != current["digest"]:
        raise _error("VM_CONFIG_STALE", "Guest configuration changed during review. Refresh before editing.")
    configured_values, current_values = _values(configured, vmtype), _values(current, vmtype)
    return VmConfigReview(host_id=row.id, node=row.node_name, vmid=vmid, vmtype=vmtype,
        digest=_digest(row, vmid, vmtype, configured["digest"]), target_digest=_config_target_digest(row, vmid, vmtype),
        current=current_values, configured=configured_values,
        pending=[key for key in FIELDS if getattr(configured_values, key) != getattr(current_values, key)]), configured["digest"]


@router.get("/hosts/{host_id}/vms/{vmid}/config/review", response_model=VmConfigReview)
async def review_vm_config(host_id: str, vmid: VmId, vmtype: Literal["qemu", "lxc"] = "qemu", session: AsyncSession = Depends(_session)):
    """Review five editable fields; current is PVE configuration, not guest telemetry."""
    row = await _get_host(host_id, session)
    _assert_vmid_safe(row, vmid, "configure")
    await _claims(session, vmid)
    async with httpx.AsyncClient(verify=proxmox_verify(), timeout=10) as cli:
        result, _ = await _read(cli, row, vmid, vmtype)
    return result


@router.put("/hosts/{host_id}/vms/{vmid}/config", response_model=VmConfigUpdateResult)
async def update_vm_config(host_id: str, vmid: VmId, body: VmConfigUpdate, vmtype: Literal["qemu", "lxc"] = "qemu", session: AsyncSession = Depends(_session)):
    """Apply a reviewed partial edit once. Unconfirmed outcomes must be refreshed,
    never blindly retried. QEMU resource edits use its asynchronous API.
    """
    with ProvisioningLock(Settings().workspace_root / ".locks"):
        # Host re-registration and lease/claim transfer cannot race the checked
        # dispatch. This is a bounded transaction with no DB data changes.
        await session.execute(text("BEGIN IMMEDIATE"))
        try:
            row = await _get_host(host_id, session)
            _assert_vmid_safe(row, vmid, "configure")
            await _claims(session, vmid)
            async with httpx.AsyncClient(verify=proxmox_verify(), timeout=10) as cli:
                before, pve_digest = await _read(cli, row, vmid, vmtype)
                if not hmac.compare_digest(before.digest, body.digest):
                    raise _error("VM_CONFIG_STALE", "The guest configuration or target registration changed. Refresh and review the edit again.")
                changes = body.changes.model_dump(exclude_unset=True)
                form = {("hostname" if key == "name" and vmtype == "lxc" else key): value for key, value in changes.items()}
                form["digest"] = pve_digest
                async_edit = vmtype == "qemu" and bool({"cores", "memory"} & changes.keys())
                try:
                    response = await (cli.post if async_edit else cli.put)(_url(row, vmid, vmtype), headers=_auth_headers(row), data=form)
                except httpx.RequestError:
                    return VmConfigUpdateResult(status="unconfirmed", reason="write_outcome_unknown")
                if response.status_code in (401, 403):
                    raise _error("VM_CONFIG_FORBIDDEN", "Proxmox denied this configuration edit.", 403)
                if response.status_code != 200:
                    # PVE can fail after partial hotplug/config work. Avoid
                    # leaking its freeform error and never invite blind retry.
                    return VmConfigUpdateResult(status="unconfirmed", reason="write_outcome_unknown")
                try:
                    value = response.json()["data"]
                except (ValueError, KeyError, TypeError):
                    return VmConfigUpdateResult(status="unconfirmed", reason="unexpected_write_response")
                if async_edit and value is not None:
                    if _config_task_vmid(value, row.node_name) == vmid:
                        return VmConfigUpdateResult(status="accepted", upid=value)
                    return VmConfigUpdateResult(status="unconfirmed", reason="unexpected_write_response")
                if value is not None:
                    return VmConfigUpdateResult(status="unconfirmed", reason="unexpected_write_response")
                try:
                    after, _ = await _read(cli, row, vmid, vmtype)
                except Range42Error:
                    return VmConfigUpdateResult(status="unconfirmed", reason="readback_unavailable")
                matches = all(getattr(after.configured, key) == value for key, value in changes.items())
                return VmConfigUpdateResult(status="configured" if matches else "unconfirmed", review=after,
                    reason=None if matches else "readback_mismatch")
        finally:
            await session.rollback()
