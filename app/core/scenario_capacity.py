"""Advisory CPU/storage budgets and measured RAM admission for concrete plans."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
import re
from urllib.parse import quote

import httpx

from app.core.host_capacity import read_host_capacity
from app.core.models import ProxmoxHost
from app.core.preflight import PreflightCheck
from app.core.proxmox_read import ProxmoxReadError, read_proxmox_data

GIB = 1024**3
PRESSURE_THRESHOLD = 0.90
_DISK = re.compile(r"(?:(?:ide|sata|scsi|virtio)\d+|efidisk0|tpmstate0)\Z")
_SIZE = re.compile(r"([0-9]+(?:\.[0-9]+)?)([KMGTPE]?)\Z")


def _check(resource: str, result: str, detail: str, code: str | None = None, *, pool=None):
    return PreflightCheck(check=f"capacity_{resource}", result=result, code=code, detail=detail,
                           field_path=f"storage:{pool}" if pool else "manifest/scenario_vms.json")


def _size_bytes(value: str) -> int | None:
    match = _SIZE.fullmatch(value)
    if not match:
        return None
    try:
        size = Decimal(match[1]) * 1024 ** ("KMGTPE".index(match[2]) + 1 if match[2] else 0)
        return int(size) if size > 0 and size == int(size) and size < 2**63 else None
    except (InvalidOperation, ValueError, OverflowError):
        return None


def _template_requirements(planned: dict, config: dict) -> tuple[int | None, dict[str, int], bool]:
    cores, sockets = planned.get("cores", config.get("cores", 1)), config.get("sockets", 1)
    cpu = cores * sockets if all(type(value) is int and value > 0 for value in (cores, sockets)) else None
    storage = defaultdict(int)
    unknown_disk = False
    found_disk = False
    resized = planned.get("disk_gb") is None
    for device, specification in config.items():
        if not _DISK.fullmatch(device):
            continue
        if not isinstance(specification, str):
            unknown_disk = True
            continue
        parts = specification.split(",")
        options = dict(part.split("=", 1) for part in parts[1:] if "=" in part)
        if options.get("media") == "cdrom":
            continue
        found_disk = True
        size = _size_bytes(options.get("size", ""))
        volume = parts[0].removeprefix("file=")
        pool, separator, _ = volume.partition(":")
        if not separator or not re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]*", pool) or size is None:
            unknown_disk = True
            continue
        if planned.get("disk_gb") is not None and device == planned.get("disk_device", "scsi0"):
            size = max(size, planned["disk_gb"] * GIB)
            resized = True
        storage[planned.get("storage") or pool] += size
    return cpu, dict(storage), unknown_disk or not found_disk or not resized


async def check_plan_capacity(client: httpx.AsyncClient, host: ProxmoxHost, vms: list[dict], *,
                               required_memory: int) -> list[PreflightCheck]:
    capacity = await read_host_capacity(host, client=client)
    free, total = capacity.memory.free_bytes, capacity.memory.total_bytes
    if free is None:
        return [_check("memory", "block", "Proxmox did not report available host memory. Check node Sys.Audit permissions and connectivity.", "SCENARIO_RESOURCES_UNREADABLE")]
    memory_detail = f"The VMs need {required_memory / 1024**2:.0f} MiB; the target currently has {free / 1024**2:.0f} MiB free."
    if required_memory > free:
        return [_check("memory", "block", memory_detail, "INSUFFICIENT_MEMORY")]
    if total is None:
        checks = [_check("memory", "warn", memory_detail + " Total memory is unknown; the pressure threshold cannot be assessed.", "MEMORY_CAPACITY_UNKNOWN")]
    elif (total - free + required_memory) / total >= PRESSURE_THRESHOLD:
        checks = [_check("memory", "warn", memory_detail + " Projected use reaches at least 90% of total RAM.", "MEMORY_PRESSURE")]
    else:
        checks = [_check("memory", "pass", memory_detail)]

    selected_pools = {vm["storage"] for vm in vms if vm.get("storage")}
    configs = {}
    for template_id in sorted({vm["template_vm_id"] for vm in vms}):
        try:
            config = await read_proxmox_data(client, host, f"/nodes/{quote(host.node_name, safe='')}/qemu/{template_id}/config")
            configs[template_id] = config if isinstance(config, dict) else None
        except ProxmoxReadError:
            configs[template_id] = None
    cpus = 0
    unknown_cpu = unknown_disk = False
    disks = defaultdict(int)
    for vm in vms:
        config = configs[vm["template_vm_id"]]
        if config is None:
            unknown_cpu = unknown_disk = True
            if vm.get("nics"):
                checks.append(_check("network", "block", "The template network configuration must be readable before cloning an explicitly authored topology.", "TEMPLATE_NETWORK_UNREADABLE"))
            if vm.get("disk_gb") is not None:
                checks.append(_check("storage", "block", "The template disk configuration must be readable before requesting growth.", "DISK_GROWTH_UNAVAILABLE"))
            continue
        if vm.get("nics"):
            planned_nics = {f"net{nic['index']}" for nic in vm["nics"]}
            inherited_nics = {key for key in config if re.fullmatch(r"net[0-9]+", key)}
            unexpected_nics = inherited_nics - planned_nics
            if unexpected_nics:
                checks.append(_check("network", "block", f"Template {vm['template_vm_id']} contains interfaces outside the authored topology: {', '.join(sorted(unexpected_nics))}. Choose a template without these interfaces or declare them in the scenario.", "TEMPLATE_NETWORK_MISMATCH"))
        if vm.get("disk_gb") is not None:
            device = vm.get("disk_device", "scsi0")
            specification = config.get(device)
            parts = specification.split(",") if isinstance(specification, str) else []
            options = dict(part.split("=", 1) for part in parts[1:] if "=" in part)
            current_size = _size_bytes(options.get("size", ""))
            if current_size is None or options.get("media") == "cdrom":
                checks.append(_check("storage", "block", f"Template disk {device} is missing, a CD-ROM, or has no readable size. Choose an existing VM disk before requesting growth.", "DISK_GROWTH_UNAVAILABLE"))
            elif vm["disk_gb"] * GIB < current_size:
                checks.append(_check("storage", "block", f"Requested disk {device} is smaller than the template disk ({current_size / GIB:g} GiB). Only existing-disk growth is supported.", "DISK_SHRINK_UNSUPPORTED"))
        cpu, storage, incomplete = _template_requirements(vm, config)
        if cpu is None:
            unknown_cpu = True
        else:
            cpus += cpu
        unknown_disk |= incomplete
        for pool, size in storage.items():
            disks[pool] += size
    if unknown_cpu:
        checks.append(_check("cpu", "warn", "Some template CPU settings are unreadable or invalid; total configured vCPU requirements are unknown.", "CPU_REQUIREMENTS_UNKNOWN"))
    elif capacity.cpu.logical_cpus is None:
        checks.append(_check("cpu", "warn", f"The plan requests {cpus} configured vCPUs, but the target's logical CPU count is unknown.", "CPU_CAPACITY_UNKNOWN"))
    else:
        detail = f"The plan requests {cpus} configured vCPUs on {capacity.cpu.logical_cpus} logical CPUs. CPU scheduling is shared; this does not reserve exclusive cores."
        if cpus > capacity.cpu.logical_cpus:
            checks.append(_check("cpu", "warn", detail, "CPU_OVERCOMMIT"))
        else:
            checks.append(_check("cpu", "pass", detail))
    if capacity.cpu.utilization is None:
        checks.append(_check("cpu", "warn", "Current CPU utilization is unavailable.", "CPU_LOAD_UNKNOWN"))
    elif capacity.cpu.utilization >= PRESSURE_THRESHOLD:
        checks.append(_check("cpu", "warn", f"The current CPU sample is {capacity.cpu.utilization:.0%} utilized before deployment.", "CPU_PRESSURE"))

    if unknown_disk:
        checks.append(_check("storage", "block" if selected_pools else "warn", "Some template disk volumes or sizes are unreadable; the full-clone estimate is incomplete. Check VM.Audit permissions and template disk configuration.", "STORAGE_REQUIREMENTS_UNKNOWN"))
    by_pool = {pool.storage: pool for pool in capacity.storage}
    for name in sorted(disks.keys() | selected_pools):
        required = disks[name]
        selected = name in selected_pools
        pool = by_pool.get(name)
        placement = "selected" if selected else "inherited"
        detail = f"Full-clone estimate for {placement} storage {name}: {required / GIB:.1f} GiB."
        if pool is None or pool.active is not True or pool.enabled is not True or pool.free_bytes is None or "images" not in pool.content:
            checks.append(_check("storage", "block" if selected else "warn", detail + " This pool's usable VM image capacity is unknown, hidden, inactive or disabled.", "STORAGE_POOL_UNAVAILABLE" if selected else "STORAGE_POOL_UNKNOWN", pool=name))
            continue
        if selected:
            try:
                path = f"/storage/{name}"
                permissions = await read_proxmox_data(client, host, "/access/permissions", params={"path": path})
                rights = permissions.get(path) if isinstance(permissions, dict) else None
                allowed = rights.get("Datastore.AllocateSpace") if isinstance(rights, dict) else None
                # Values are propagation flags: a present privilege with 0
                # still grants this exact pool, but not its descendants.
                if type(allowed) not in (bool, int) or allowed not in (0, 1):
                    raise ValueError("Allocation permission unavailable")
            except (ProxmoxReadError, ValueError):
                checks.append(_check("storage", "block", detail + " The selected token must have Datastore.AllocateSpace on this pool.", "STORAGE_PERMISSION_UNAVAILABLE", pool=name))
                continue
            if unknown_disk:
                # An incomplete estimate cannot establish capacity, even when
                # the selected pool itself is available and writable.
                continue
        detail += f" It currently has {pool.free_bytes / GIB:.1f} GiB free. Thin provisioning and snapshot overhead can change the actual allocation."
        if not selected:
            detail += " Legacy runtime storage overrides may also change placement."
        if required > pool.free_bytes:
            checks.append(_check("storage", "block" if selected else "warn", detail, "INSUFFICIENT_STORAGE" if selected else "STORAGE_ESTIMATE_EXCEEDS_FREE", pool=name))
        elif pool.total_bytes is not None and (pool.total_bytes - pool.free_bytes + required) / pool.total_bytes >= PRESSURE_THRESHOLD:
            checks.append(_check("storage", "warn", detail + " Projected use reaches at least 90% of the pool.", "STORAGE_PRESSURE", pool=name))
        elif pool.total_bytes is None:
            checks.append(_check("storage", "warn", detail + " Total pool size is unknown.", "STORAGE_CAPACITY_UNKNOWN", pool=name))
        else:
            checks.append(_check("storage", "pass", detail, pool=name))
    return checks
