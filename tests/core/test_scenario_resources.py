"""Generated VM plans must use valid templates and preserve existing resources."""
import json

import httpx
import pytest

from tests.core.test_scenario_networks import target


async def check(tmp_path, data, *, scope="full", marker="range42-deployment:dep-1", occupied=False, planned=None, firewall=None):
    from app.core.scenario_resources import check_scenario_resources
    (tmp_path / "manifest").mkdir()
    if firewall is not None:
        (tmp_path / "manifest/scenario_firewall.json").write_text(json.dumps(firewall))
    (tmp_path / "manifest/scenario_vms.json").write_text(json.dumps({"vms": [
        {"vm_id": 3191, "vm_name": "r42-ui-smoke", "template_vm_id": 9901, **(planned or {})},
    ]}))
    requests = []
    def handle(request):
        assert request.method == "GET"
        requests.append(request)
        path = request.url.path
        if path.endswith("/cluster/resources"):
            return httpx.Response(200, json={"data": data})
        if path.endswith("/cluster/nextid"):
            assert request.url.params["vmid"] == "3191"
            return httpx.Response(400, text="already exists") if occupied else httpx.Response(200, json={"data": "3191"})
        if path.endswith("/status"):
            return httpx.Response(200, json={"data": {"cpuinfo": {"cpus": 8}, "cpu": 0.2,
                "memory": {"total": 8 * 1024**3, "used": 4 * 1024**3, "free": 4 * 1024**3}}})
        if path.endswith("/storage"):
            return httpx.Response(200, json={"data": [{"storage": "local-lvm", "type": "lvmthin", "content": "images",
                "active": 1, "enabled": 1, "shared": 0, "total": 100 * 1024**3, "used": 20 * 1024**3, "avail": 80 * 1024**3}]})
        if path.endswith("/9901/config"):
            return httpx.Response(200, json={"data": {"cores": 2, "scsi0": "local-lvm:base-9901-disk-0,size=10G"}})
        if path.endswith("/config"):
            return httpx.Response(200, json={"data": {"name": "r42-ui-smoke", "description": marker}})
        return httpx.Response(404)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await check_scenario_resources(tmp_path, target(), deployment_id="dep-1", scope=scope, client=client)
    return result, requests


def template(**overrides):
    return {"vmid": 9901, "node": "pve01", "name": "template", "type": "qemu", "template": 1,
            "maxmem": 1024**3, **overrides}


def vm(**overrides):
    return {"vmid": 3191, "node": "pve01", "name": "r42-ui-smoke", "type": "qemu", **overrides}


@pytest.mark.asyncio
async def test_host_management_rules_require_explicit_installation_authority(tmp_path, monkeypatch):
    from app.core import runtime_operations
    monkeypatch.setattr(runtime_operations, "operation_profile", lambda kind: {"contract": "native-sdn-20260921"})
    monkeypatch.delenv("RANGE42_SCENARIO_MANAGEMENT_ACCESS", raising=False)
    result, requests = await check(tmp_path, [template()], firewall={
        "version": 1, "prepare_management_access": True, "arm_vms": False, "ssh_sources": None,
    })
    assert result[-1].code == "FIREWALL_MANAGEMENT_NOT_AUTHORIZED"
    assert not requests


@pytest.mark.asyncio
async def test_native_firewall_stages_require_the_recognized_contract(tmp_path, monkeypatch):
    from app.core import runtime_operations
    monkeypatch.setattr(runtime_operations, "operation_profile", lambda kind: {"operations": ["vm_firewall"]})
    result, requests = await check(tmp_path, [template()], firewall={
        "version": 1, "prepare_management_access": False, "arm_vms": False, "ssh_sources": None,
    })
    assert result[-1].code == "FIREWALL_RUNTIME_UNSUPPORTED"
    assert not requests


@pytest.mark.asyncio
@pytest.mark.parametrize("sources", [["203.0.113.7/24"], ["{{ secret }}"], "203.0.113.7/32"])
async def test_unsafe_firewall_preferences_block_before_pve(tmp_path, monkeypatch, sources):
    from app.core import runtime_operations
    monkeypatch.setattr(runtime_operations, "operation_profile", lambda kind: {"contract": "native-sdn-20260921"})
    result, requests = await check(tmp_path, [template()], firewall={
        "version": 1, "prepare_management_access": False, "arm_vms": False, "ssh_sources": sources,
    })
    assert result[-1].code == "FIREWALL_POLICY_INVALID"
    assert not requests


@pytest.mark.asyncio
async def test_unused_vm_and_existing_template_pass(tmp_path):
    result, _ = await check(tmp_path, [template()])
    assert result and all(item.result == "pass" for item in result)


@pytest.mark.asyncio
@pytest.mark.parametrize("rows,code", [
    ([], "TEMPLATE_NOT_READY"), ([template(template=0)], "TEMPLATE_NOT_READY"),
    ([template(node="other")], "TEMPLATE_NOT_READY"), ([template(type="lxc")], "TEMPLATE_NOT_READY"),
    ([template(), vm()], "VMID_IN_USE"), ([template(), vm(node="other")], "VMID_IN_USE"),
    ([template(maxmem=8 * 1024**3)], "INSUFFICIENT_MEMORY"),
])
async def test_invalid_bootstrap_resources_block(tmp_path, rows, code):
    result, _ = await check(tmp_path, rows)
    assert result and result[-1].code == code
    assert result[-1].result == "block"


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["configure", "teardown"])
async def test_existing_owned_vm_can_be_configured_or_removed_without_source_template(tmp_path, scope):
    result, requests = await check(tmp_path, [vm()], scope=scope)
    assert result and all(item.result == "pass" for item in result)
    assert any(request.url.path.endswith("/config") for request in requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", ["", "range42-deployment:other", "range42-deployment:dep-123"])
async def test_teardown_requires_exact_deployment_ownership_marker(tmp_path, marker):
    result, _ = await check(tmp_path, [vm()], scope="teardown", marker=marker)
    assert result and result[-1].code == "VM_OWNERSHIP_MISMATCH"


@pytest.mark.asyncio
async def test_teardown_does_not_delete_vm_reassigned_to_another_node(tmp_path):
    result, _ = await check(tmp_path, [vm(node="other")], scope="teardown")
    assert result and result[-1].code == "VM_OWNERSHIP_MISMATCH"


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["full", "teardown"])
async def test_vm_hidden_by_audit_permissions_cannot_be_treated_as_absent(tmp_path, scope):
    result, requests = await check(tmp_path, [template()], scope=scope, occupied=True)
    assert result[-1].result == "block"
    assert any(request.url.path.endswith("/cluster/nextid") for request in requests)


@pytest.mark.asyncio
async def test_teardown_skips_vm_only_after_cluster_confirms_global_absence(tmp_path):
    result, requests = await check(tmp_path, [], scope="teardown")
    assert result[-1].result == "pass"
    assert any(request.url.path.endswith("/cluster/nextid") for request in requests)


def capabilities(tmp_path, monkeypatch, features):
    bundle = tmp_path / "bundles/proxmox/vm.bootstrap"
    bundle.mkdir(parents=True)
    (bundle / "capabilities.json").write_text(json.dumps({"version": 1, "features": features}))
    monkeypatch.setenv("RANGE42_BUNDLE_DIR", str(tmp_path / "bundles"))


@pytest.mark.asyncio
@pytest.mark.parametrize("planned", [
    {"cores": 4}, {"memory_mb": 8192}, {"disk_gb": 30, "disk_device": "scsi0"},
    {"nics": [{"index": 0, "ip": "10.1.0.2", "bridge": "vnet1"}, {"index": 1, "ip": "10.2.0.2", "bridge": "vnet2"}]},
])
async def test_extended_plan_requires_matching_installed_bootstrap(tmp_path, monkeypatch, planned):
    capabilities(tmp_path, monkeypatch, [])
    result, requests = await check(tmp_path, [template()], planned=planned)
    assert result[-1].code == "BOOTSTRAP_CAPABILITY_MISSING"
    assert not requests


@pytest.mark.asyncio
async def test_memory_override_is_used_for_host_capacity(tmp_path, monkeypatch):
    capabilities(tmp_path, monkeypatch, ["resources"])
    result, _ = await check(tmp_path, [template()], planned={"memory_mb": 8192})
    assert result[-1].code == "INSUFFICIENT_MEMORY"
    assert "8192 MiB" in result[-1].detail


@pytest.mark.asyncio
async def test_smaller_explicit_memory_override_replaces_template_memory(tmp_path, monkeypatch):
    capabilities(tmp_path, monkeypatch, ["resources"])
    result, _ = await check(tmp_path, [template(maxmem=8 * 1024**3)], planned={"memory_mb": 1024})
    assert result[-1].result == "pass"


@pytest.mark.asyncio
async def test_configure_does_not_require_bootstrap_capabilities(tmp_path, monkeypatch):
    capabilities(tmp_path, monkeypatch, [])
    result, _ = await check(tmp_path, [vm()], scope="configure", planned={"memory_mb": 8192})
    assert result[-1].result == "pass"


@pytest.mark.asyncio
@pytest.mark.parametrize("planned", [{"memory_mb": -1}, {"cores": 1.5}, {"disk_gb": True}])
async def test_legacy_manifest_cannot_bypass_resource_override_validation(tmp_path, monkeypatch, planned):
    capabilities(tmp_path, monkeypatch, ["resources", "disk_resize"])
    result, _ = await check(tmp_path, [template()], planned=planned)
    assert result[-1].code == "SCENARIO_RESOURCES_INVALID"
