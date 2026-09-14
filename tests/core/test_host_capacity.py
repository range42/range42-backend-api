"""Read-only capacity observations retain unavailable and permission-filtered data."""
import httpx
import pytest

from tests.core.test_scenario_networks import target

GIB = 1024**3


def node_status(**overrides):
    return {"cpuinfo": {"cpus": 8}, "cpu": 0.25,
            "memory": {"total": 16 * GIB, "used": 4 * GIB, "free": 12 * GIB}, **overrides}


def storage(**overrides):
    return {"storage": "local-lvm", "type": "lvmthin", "content": "images,rootdir",
            "enabled": 1, "active": 1, "shared": 0,
            "total": 100 * GIB, "used": 25 * GIB, "avail": 75 * GIB, **overrides}


async def read(status=None, pools=None, *, node_code=200, storage_code=200):
    from app.core.host_capacity import read_host_capacity
    calls = []

    def respond(request):
        assert request.method == "GET"
        calls.append(request.url.path)
        if request.url.path.endswith("/status"):
            return httpx.Response(node_code, json={"data": status if status is not None else node_status()})
        assert request.url.path.endswith("/storage")
        return httpx.Response(storage_code, json={"data": pools if pools is not None else [storage()]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await read_host_capacity(target(), client=client)
    return result.model_dump(), calls


@pytest.mark.asyncio
async def test_reports_hardware_cpu_load_ram_and_storage_without_inventing_free_cores():
    result, calls = await read()
    assert result["status"] == "available"
    assert result["cpu"] == {"logical_cpus": 8, "utilization": 0.25}
    assert result["memory"]["free_bytes"] == 12 * GIB
    assert result["storage"][0]["free_bytes"] == 75 * GIB
    assert result["storage"][0]["content"] == ["images", "rootdir"]
    assert result["observed_at"] and result["limitations"]
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_permission_failure_keeps_other_measurements_and_unknown_ram():
    result, _ = await read(node_code=403)
    assert result["status"] == "partial"
    assert result["memory"]["free_bytes"] is None
    assert result["cpu"]["logical_cpus"] is None
    assert result["storage"][0]["free_bytes"] == 75 * GIB
    assert any(issue["code"] == "NODE_CAPACITY_UNAVAILABLE" for issue in result["issues"])


@pytest.mark.asyncio
async def test_all_upstream_failures_are_unavailable_not_zero():
    result, _ = await read(node_code=403, storage_code=500)
    assert result["status"] == "unavailable"
    assert result["storage"] == []
    assert result["memory"]["free_bytes"] is None
    assert len(result["issues"]) == 2


@pytest.mark.asyncio
async def test_empty_storage_list_is_possibly_permission_filtered():
    result, _ = await read(pools=[])
    assert result["status"] == "partial"
    assert any(issue["code"] == "STORAGE_VISIBILITY_UNKNOWN" for issue in result["issues"])


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [-1, "0", True, float("inf")])
async def test_invalid_numeric_measurements_stay_unknown(value):
    # Infinity is invalid JSON, so exercise it as decoded data in the parser.
    from app.core.host_capacity import normalize_node_capacity
    cpu, memory, issues = normalize_node_capacity(node_status(memory={"total": 16 * GIB, "free": value}))
    assert memory.free_bytes is None
    assert issues


@pytest.mark.asyncio
async def test_zero_free_memory_and_disk_are_real_exhaustion_measurements():
    result, _ = await read(status=node_status(memory={"total": 16 * GIB, "used": 16 * GIB, "free": 0}),
                           pools=[storage(used=100 * GIB, avail=0)])
    assert result["status"] == "available"
    assert result["memory"]["free_bytes"] == 0
    assert result["storage"][0]["free_bytes"] == 0


@pytest.mark.asyncio
async def test_inactive_pool_does_not_report_its_stale_free_value_as_usable():
    result, _ = await read(pools=[storage(active=0)])
    assert result["storage"][0]["active"] is False
    assert result["storage"][0]["free_bytes"] is None
    assert any(issue["code"] == "STORAGE_INACTIVE" for issue in result["issues"])
