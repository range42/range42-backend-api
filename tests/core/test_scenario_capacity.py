"""Full deployment budgets compare to measured resources without exclusive CPU claims."""
import json

import httpx
import pytest

from tests.core.test_host_capacity import GIB, node_status, storage
from tests.core.test_scenario_networks import target


async def check(tmp_path, monkeypatch, *, planned=None, vms=None, status=None, pools=None, config=None, config_code=200, scope="full"):
    from app.core.scenario_resources import check_scenario_resources
    (tmp_path / "manifest").mkdir()
    (tmp_path / "manifest/scenario_vms.json").write_text(json.dumps({"vms": vms if vms is not None else [
        {"vm_id": 3191, "vm_name": "r42-budget", "template_vm_id": 9901, **(planned or {})},
    ]}))
    capabilities = tmp_path / "bundles/proxmox/vm.bootstrap"
    capabilities.mkdir(parents=True)
    (capabilities / "capabilities.json").write_text(json.dumps({"version": 1, "features": ["resources", "disk_resize"]}))
    monkeypatch.setenv("RANGE42_BUNDLE_DIR", str(tmp_path / "bundles"))
    calls = []

    def respond(request):
        assert request.method == "GET"
        calls.append(request.url.path)
        if request.url.path.endswith("/resources"):
            rows = [{"vmid": 9901, "template": 1, "type": "qemu", "node": "pve01", "maxmem": GIB}]
            if scope != "full":
                rows.append({"vmid": 3191, "type": "qemu", "node": "pve01", "name": "r42-budget"})
            return httpx.Response(200, json={"data": rows})
        if request.url.path.endswith("/nextid"):
            return httpx.Response(200, json={"data": request.url.params["vmid"]})
        if request.url.path.endswith("/status"):
            return httpx.Response(200, json={"data": status if status is not None else node_status()})
        if request.url.path.endswith("/storage"):
            return httpx.Response(200, json={"data": pools if pools is not None else [storage()]})
        if "/3191/config" in request.url.path:
            return httpx.Response(200, json={"data": {"description": "range42-deployment:dep"}})
        if "/9901/config" in request.url.path:
            return httpx.Response(config_code, json={"data": config if config is not None else {
                "cores": 2, "sockets": 1, "scsi0": "local-lvm:base-9901-disk-0,size=10G"}})
        raise AssertionError(request.url)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        checks = await check_scenario_resources(tmp_path, target(), deployment_id="dep", scope=scope, client=client)
    return checks, calls


@pytest.mark.asyncio
async def test_full_plan_reports_cpu_memory_and_disk_checks(tmp_path, monkeypatch):
    checks, _ = await check(tmp_path, monkeypatch)
    for name in ("capacity_cpu", "capacity_memory", "capacity_storage"):
        assert any(item.check == name and item.result == "pass" for item in checks)


@pytest.mark.asyncio
async def test_zero_available_ram_blocks_with_real_insufficiency(tmp_path, monkeypatch):
    checks, _ = await check(tmp_path, monkeypatch, status=node_status(memory={"total": 16 * GIB, "used": 16 * GIB, "free": 0}))
    assert checks[-1].code == "INSUFFICIENT_MEMORY"
    assert "0 MiB free" in checks[-1].detail


@pytest.mark.asyncio
async def test_cpu_override_inherits_template_sockets_and_warns_about_overcommit(tmp_path, monkeypatch):
    checks, _ = await check(tmp_path, monkeypatch, planned={"cores": 6}, config={"cores": 1, "sockets": 2, "scsi0": "local-lvm:base-1,size=10G"})
    cpu = next(item for item in checks if item.check == "capacity_cpu")
    assert cpu.result == "warn" and cpu.code == "CPU_OVERCOMMIT"
    assert "12 configured vCPUs" in cpu.detail and "8 logical CPUs" in cpu.detail


@pytest.mark.asyncio
async def test_projected_ram_threshold_warns_before_total_exhaustion(tmp_path, monkeypatch):
    checks, _ = await check(tmp_path, monkeypatch, planned={"memory_mb": 11 * 1024})
    assert any(item.code == "MEMORY_PRESSURE" and item.result == "warn" for item in checks)


@pytest.mark.asyncio
async def test_full_clone_estimate_includes_all_disks_and_requested_growth(tmp_path, monkeypatch):
    checks, _ = await check(tmp_path, monkeypatch, planned={"disk_gb": 20, "disk_device": "scsi0"},
                           pools=[storage(total=100 * GIB, used=76 * GIB, avail=24 * GIB)],
                           config={"cores": 2, "scsi0": "local-lvm:base-0,size=10G", "scsi1": "local-lvm:base-1,size=8G",
                                   "ide2": "local:iso/installer.iso,media=cdrom,size=2G"})
    disk = next(item for item in checks if item.code == "STORAGE_ESTIMATE_EXCEEDS_FREE")
    assert disk.result == "warn"
    assert "28.0 GiB" in disk.detail and "24.0 GiB free" in disk.detail
    assert not any("storage:local" == item.field_path for item in checks)


@pytest.mark.asyncio
async def test_storage_threshold_and_hidden_pool_are_reported_per_pool(tmp_path, monkeypatch):
    checks, _ = await check(tmp_path, monkeypatch, pools=[storage(used=85 * GIB, avail=15 * GIB)],
                           config={"cores": 2, "scsi0": "local-lvm:base-0,size=10G", "scsi1": "hidden:base-1,size=8G"})
    assert any(item.code == "STORAGE_PRESSURE" for item in checks)
    assert any(item.code == "STORAGE_POOL_UNKNOWN" and "hidden" in item.detail for item in checks)


@pytest.mark.asyncio
async def test_template_permission_error_warns_without_inventing_cpu_or_disk_needs(tmp_path, monkeypatch):
    checks, _ = await check(tmp_path, monkeypatch, config_code=403)
    assert any(item.code == "CPU_REQUIREMENTS_UNKNOWN" for item in checks)
    assert any(item.code == "STORAGE_REQUIREMENTS_UNKNOWN" for item in checks)
    assert not any(item.check in ("capacity_cpu", "capacity_storage") and item.result == "pass" for item in checks)


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["configure", "teardown"])
async def test_non_provisioning_scopes_only_check_ownership(tmp_path, monkeypatch, scope):
    checks, calls = await check(tmp_path, monkeypatch, scope=scope)
    assert not any(item.check.startswith("capacity_") for item in checks)
    assert not any(path.endswith(("/status", "/storage")) or "/9901/config" in path for path in calls)


@pytest.mark.asyncio
async def test_each_vm_counts_against_budget_while_template_reads_are_reused(tmp_path, monkeypatch):
    vms = [{"vm_id": vmid, "vm_name": f"budget-{vmid}", "template_vm_id": 9901} for vmid in (3191, 3192, 3193)]
    checks, calls = await check(tmp_path, monkeypatch, vms=vms, pools=[storage(used=76 * GIB, avail=24 * GIB)])
    assert sum("/9901/config" in path for path in calls) == 1
    assert any(item.check == "capacity_cpu" and "6 configured vCPUs" in item.detail for item in checks)
    assert any(item.check == "capacity_memory" and "3072 MiB" in item.detail for item in checks)
    assert any(item.code == "STORAGE_ESTIMATE_EXCEEDS_FREE" and "30.0 GiB" in item.detail for item in checks)


@pytest.mark.asyncio
async def test_cpu_sample_pressure_does_not_invent_exclusive_core_availability(tmp_path, monkeypatch):
    checks, _ = await check(tmp_path, monkeypatch, status=node_status(cpu=0.99))
    assert any(item.code == "CPU_PRESSURE" and "99%" in item.detail for item in checks)
    assert not any(item.result == "block" for item in checks)
