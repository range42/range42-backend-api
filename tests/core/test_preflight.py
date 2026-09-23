from app.core.preflight import (
    PreflightReport,
    check_resource_budget,
    check_secret_completeness,
    check_vmids,
)
import httpx
import pytest


def test_vmids_block_on_protected():
    r = check_vmids([100, 200], host_overrides=None)
    assert r.result == "block"
    assert "protected" in r.detail.lower()


def test_vmids_block_on_duplicate():
    r = check_vmids([200, 200], host_overrides=None)
    assert r.result == "block"


def test_vmids_pass_on_clean_set():
    r = check_vmids([200, 201, 202], host_overrides=None)
    assert r.result == "pass"


def test_resource_budget_warn_at_95_pct():
    r = check_resource_budget(total_ram_mb_required=95000, host_total_ram_mb=100000)
    assert r.result == "warn"


def test_resource_budget_block_over_110():
    r = check_resource_budget(total_ram_mb_required=112000, host_total_ram_mb=100000)
    assert r.result == "block"


def test_resource_budget_block_on_zero_capacity():
    r = check_resource_budget(total_ram_mb_required=100, host_total_ram_mb=0)
    assert r.result == "block"


def test_resource_budget_pass_under_90():
    r = check_resource_budget(total_ram_mb_required=50000, host_total_ram_mb=100000)
    assert r.result == "pass"


def test_secret_completeness_detects_missing():
    env = [{"name": "admin_password", "secret": True, "required": True}]
    r = check_secret_completeness(env, provided={})
    assert r.result == "block"
    r = check_secret_completeness(env, provided={"admin_password": "x"})
    assert r.result == "pass"


def test_secret_completeness_ignores_non_secret():
    env = [{"name": "some_var", "secret": False, "required": True}]
    r = check_secret_completeness(env, provided={})
    assert r.result == "pass"


def test_report_aggregates_block_over_warn():
    rep = PreflightReport()
    rep.checks.append(check_resource_budget(total_ram_mb_required=95000, host_total_ram_mb=100000))  # warn
    rep.checks.append(check_vmids([100], host_overrides=None))  # block
    assert rep.result == "block"


def test_report_aggregates_warn_when_no_block():
    rep = PreflightReport()
    rep.checks.append(check_resource_budget(total_ram_mb_required=95000, host_total_ram_mb=100000))  # warn
    rep.checks.append(check_vmids([200], host_overrides=None))  # pass
    assert rep.result == "warn"


def test_report_pass_when_all_pass():
    rep = PreflightReport()
    rep.checks.append(check_vmids([200], host_overrides=None))
    rep.checks.append(check_resource_budget(total_ram_mb_required=10, host_total_ram_mb=1000))
    assert rep.result == "pass"


@pytest.mark.asyncio
@pytest.mark.parametrize("trailing_slash", ["", "/"])
async def test_registered_host_url_works_for_connectivity_and_sdn(monkeypatch, trailing_slash):
    from app.core import preflight
    paths = []
    def handle(request):
        paths.append(request.url.path)
        if request.url.path == "/api2/json/nodes":
            return httpx.Response(200, json={"data": [{"node": "pve01"}]})
        if request.url.path == "/api2/json/cluster/sdn/vnets":
            return httpx.Response(200, json={"data": [{"vnet": "r42blue"}]})
        return httpx.Response(500)
    client_factory = httpx.AsyncClient
    monkeypatch.setattr(preflight.httpx, "AsyncClient", lambda **kwargs: client_factory(transport=httpx.MockTransport(handle), **kwargs))
    url = f"https://pve.example:8006{trailing_slash}"
    assert (await preflight.check_proxmox_api_status(url, "token")).result == "pass"
    assert (await preflight.check_sdn_bridge(url, "token", "r42blue")).result == "pass"
    assert paths == ["/api2/json/nodes", "/api2/json/cluster/sdn/vnets"]
