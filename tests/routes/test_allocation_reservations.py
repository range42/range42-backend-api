"""Durable authoring allocation against a real SQLite DB and read-only fake PVE."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from importlib import reload

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

TOKEN = "authoring-owner-token-0123456789abcdef"
OTHER_TOKEN = "different-owner-token-0123456789abcdef"
HEADERS = {"X-Range42-Reservation-Token": TOKEN}


def plan(project="local-draft-1"):
    return {
        "project_key": project, "vmid_start": 2000, "vmid_end": 2100,
        "networks": [{"network_id": "lab", "bridge": "labnet", "subnet": "10.42.8.0/29", "gateway": "10.42.8.1"}],
        "vms": [{"node_id": "vm-a", "nics": [{"index": 0, "network_id": "lab"}]}],
    }


class Proxmox:
    def __init__(self):
        self.resources = []
        self.configs = {}
        self.occupied = set()
        self.probes = []
        self.audit = 1
        self.nextid_status = None
        self.networks = [{"iface": "vmbr0", "cidr": "192.168.142.190/24"}]

    def respond(self, request):
        assert request.method == "GET", "Allocation must never mutate Proxmox"
        path = request.url.path.removeprefix("/api2/json")
        if path == "/access/permissions":
            data = {"/vms": {"VM.Audit": self.audit}}
        elif path == "/cluster/resources":
            data = self.resources
        elif path == "/nodes":
            data = [{"node": "pve01", "status": "online"}]
        elif path.endswith("/network"):
            data = self.networks
        elif path.endswith("/config"):
            vmid = int(path.split("/")[-2])
            data = self.configs[vmid]
        elif path == "/cluster/nextid":
            vmid = int(request.url.params["vmid"])
            self.probes.append(vmid)
            if self.nextid_status:
                return httpx.Response(self.nextid_status, json={"errors": {"secret": "must not leak"}})
            if vmid in self.occupied:
                return httpx.Response(400, json={"errors": {"vmid": f"VM {vmid} already exists"}})
            data = vmid
        else:
            raise AssertionError(f"Unexpected Proxmox read {path}")
        return httpx.Response(200, json={"data": data})


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RANGE42_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'alloc.db'}")
    monkeypatch.setenv("RANGE42_WORKSPACE_ROOT", str(tmp_path))
    installed = tmp_path / "playbooks"
    (installed / "scenarios").mkdir(parents=True)
    (installed / "scenarios" / "_reserved.json").write_text("")
    monkeypatch.setenv("API_BACKEND_WWWAPP_PLAYBOOKS_DIR", str(installed))
    from app.core import config, db
    reload(config)
    reload(db)
    from app.main import create_app
    app = create_app()
    from app.core.models import Base
    async with db.get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    pve = Proxmox()
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: AsyncClient(transport=httpx.MockTransport(pve.respond)))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/proxmox/hosts", json={
            "name": "pve01", "api_url": "https://pve01:8006", "node_name": "pve01",
            "token_ref": "root@pam!test=fixture", "protected_vmids_override": [[2001, 2002]],
        })
        assert response.status_code == 201
        host_id = response.json()["id"]
        yield client, pve, db, f"/v1/proxmox/hosts/{host_id}/reservations"
    await db.dispose_engine()


@pytest.mark.asyncio
async def test_retry_after_database_restart_retains_mapping_and_uses_local_project(env):
    client, pve, db, url = env
    first = await client.post(url, json=plan(), headers=HEADERS)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["assignments"][0]["vm_id"] == 2000
    assert body["assignments"][0]["nics"][0]["ip"] == "10.42.8.2"
    assert body["limitations"]
    await db.dispose_engine()
    second = await client.post(url, json=plan(), headers=HEADERS)
    assert second.status_code == 200, second.text
    assert second.json()["reservation_id"] == body["reservation_id"]
    assert second.json()["assignments"] == body["assignments"]
    assert pve.probes == [2000, 2000]
    async with db.get_engine().connect() as conn:
        records = (await conn.execute(text("SELECT token_hash, assignments FROM allocation_reservations"))).all()
    assert len(records) == 1
    assert TOKEN not in str(records)


@pytest.mark.asyncio
async def test_concurrent_projects_reserve_distinct_vmids_and_ips(env):
    client, _, _, url = env
    responses = await asyncio.gather(*[
        client.post(url, json=plan(f"draft-{index}"), headers=HEADERS) for index in range(4)
    ])
    assert [r.status_code for r in responses] == [200] * 4, [r.text for r in responses]
    rows = [r.json()["assignments"][0] for r in responses]
    assert {r["vm_id"] for r in rows} == {2000, 2003, 2004, 2005}
    assert {r["nics"][0]["ip"] for r in rows} == {f"10.42.8.{i}" for i in range(2, 6)}


@pytest.mark.asyncio
async def test_hidden_occupied_vmid_is_skipped_using_global_probe(env):
    client, pve, _, url = env
    pve.occupied = {2000, 2003}
    response = await client.post(url, json=plan(), headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json()["assignments"][0]["vm_id"] == 2004
    assert pve.probes == [2000, 2003, 2004]


@pytest.mark.asyncio
@pytest.mark.parametrize(("vmid", "code"), [(9000, "ALLOCATION_PROTECTED"), (2001, "ALLOCATION_PROTECTED"), (2000, "ALLOCATION_OCCUPIED")])
async def test_explicit_protected_or_occupied_vmids_are_not_silently_changed(env, vmid, code):
    client, pve, _, url = env
    pve.occupied = {2000}
    request = plan()
    request["vms"][0]["vm_id"] = vmid
    response = await client.post(url, json=request, headers=HEADERS)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == code


@pytest.mark.asyncio
async def test_new_vm_retains_existing_assignment_and_manual_values(env):
    client, _, _, url = env
    request = plan()
    request["vms"][0]["vm_id"] = 2050
    request["vms"][0]["nics"][0]["ip"] = "10.42.8.5"
    first = await client.post(url, json=request, headers=HEADERS)
    assert first.status_code == 200, first.text
    request = plan()
    request["vms"].append({"node_id": "vm-b", "nics": [{"index": 0, "network_id": "lab"}]})
    second = await client.post(url, json=request, headers=HEADERS)
    assert second.status_code == 200, second.text
    a, b = second.json()["assignments"]
    assert (a["vm_id"], a["nics"][0]["ip"]) == (2050, "10.42.8.5")
    assert (b["vm_id"], b["nics"][0]["ip"]) == (2000, "10.42.8.2")


@pytest.mark.asyncio
async def test_cloudinit_secondary_nic_and_lxc_addresses_are_occupied(env):
    client, pve, _, url = env
    pve.resources = [{"vmid": 5000, "type": "qemu", "node": "pve01"}, {"vmid": 5001, "type": "lxc", "node": "pve01"}]
    pve.configs = {
        5000: {"net1": "virtio=00:11:22:33:44:55,bridge=labnet", "ipconfig1": "ip=10.42.8.2/29,gw=10.42.8.1"},
        5001: {"net0": "name=eth0,bridge=labnet,ip=10.42.8.3/29"},
    }
    response = await client.post(url, json=plan(), headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json()["assignments"][0]["nics"][0]["ip"] == "10.42.8.4"


@pytest.mark.asyncio
async def test_manual_address_collision_on_different_nic_rejected(env):
    client, _, _, url = env
    request = plan()
    request["vms"][0]["nics"][0]["ip"] = "10.42.8.4"
    request["vms"][0]["nics"].append({"index": 1, "network_id": "lab", "ip": "10.42.8.4"})
    response = await client.post(url, json=request, headers=HEADERS)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "ALLOCATION_OCCUPIED"


@pytest.mark.asyncio
@pytest.mark.parametrize("audit", [0, None])
async def test_incomplete_guest_audit_permissions_block_address_claims(env, audit):
    client, pve, _, url = env
    pve.audit = audit
    response = await client.post(url, json=plan(), headers=HEADERS)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "ALLOCATION_OCCUPANCY_UNAVAILABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 500])
async def test_global_availability_errors_are_not_treated_as_free(env, status):
    client, pve, _, url = env
    pve.nextid_status = status
    response = await client.post(url, json=plan(), headers=HEADERS)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "ALLOCATION_OCCUPANCY_UNAVAILABLE"
    assert "must not leak" not in response.text


@pytest.mark.asyncio
async def test_other_owner_cannot_read_change_or_release_reservation(env):
    client, _, _, url = env
    response = await client.post(url, json=plan(), headers=HEADERS)
    assert response.status_code == 200, response.text
    reservation_url = f"{url}/{response.json()['reservation_id']}"
    wrong = {"X-Range42-Reservation-Token": OTHER_TOKEN}
    for method, target, kwargs in [("POST", url, {"json": plan()}), ("GET", reservation_url, {}), ("DELETE", reservation_url, {})]:
        rejected = await client.request(method, target, headers=wrong, **kwargs)
        assert rejected.status_code == 403, rejected.text
        assert rejected.json()["code"] == "ALLOCATION_OWNERSHIP"
    read = await client.get(reservation_url, headers=HEADERS)
    assert read.status_code == 200
    released = await client.delete(reservation_url, headers=HEADERS)
    assert released.status_code == 204
    other = await client.post(url, json=plan("other-project"), headers=wrong)
    assert other.status_code == 200
    assert other.json()["assignments"][0]["vm_id"] == 2000


@pytest.mark.asyncio
async def test_expired_reservation_is_reclaimed(env):
    client, _, db, url = env
    response = await client.post(url, json=plan(), headers=HEADERS)
    assert response.status_code == 200, response.text
    reservation_id = response.json()["reservation_id"]
    async with db.get_engine().begin() as conn:
        await conn.execute(text("UPDATE allocation_reservations SET expires_at = :expired"), {"expired": datetime.now(timezone.utc) - timedelta(seconds=1)})
    expired = await client.get(f"{url}/{reservation_id}", headers=HEADERS)
    assert expired.status_code == 409
    assert expired.json()["code"] == "ALLOCATION_EXPIRED"
    other = await client.post(url, json=plan("other-project"), headers=HEADERS)
    assert other.status_code == 200, other.text
    assert other.json()["assignments"][0]["vm_id"] == 2000


@pytest.mark.asyncio
async def test_pool_exhaustion_rolls_back_entire_reservation(env):
    client, _, db, url = env
    request = plan()
    request["networks"][0]["reserved_ips"] = [f"10.42.8.{i}" for i in range(2, 7)]
    response = await client.post(url, json=request, headers=HEADERS)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "ALLOCATION_POOL_EXHAUSTED"
    async with db.get_engine().connect() as conn:
        count = (await conn.execute(text("SELECT COUNT(*) FROM allocation_reservations"))).scalar_one()
    assert count == 0


@pytest.mark.asyncio
async def test_reserved_gateway_and_node_address_cannot_be_requested(env):
    client, pve, _, url = env
    pve.networks = [{"iface": "labnet", "cidr": "10.42.8.3/29"}]
    for address in ["10.42.8.0", "10.42.8.1", "10.42.8.3", "10.42.8.7"]:
        request = plan()
        request["vms"][0]["nics"][0]["ip"] = address
        response = await client.post(url, json=request, headers=HEADERS)
        assert response.status_code == 409, response.text
        assert response.json()["code"] in {"ALLOCATION_INVALID", "ALLOCATION_OCCUPIED"}


@pytest.mark.asyncio
async def test_plan_limits_and_types_rejected_before_proxmox_reads(env):
    client, pve, _, url = env
    bad = []
    for key, value in [("lease_seconds", 86401), ("vmid_start", "2000")]:
        request = plan()
        request[key] = value
        bad.append(request)
    request = plan()
    request["vms"][0]["nics"][0]["index"] = 1
    bad.append(request)
    request = plan()
    request["networks"].append({"network_id": "overlap", "bridge": "other", "subnet": "10.42.8.0/28"})
    bad.append(request)
    for request in bad:
        response = await client.post(url, json=deepcopy(request), headers=HEADERS)
        assert response.status_code in (409, 422), response.text
    assert pve.probes == []


@pytest.mark.asyncio
async def test_independent_database_engines_serialize_reservations(env):
    client, pve, db, url = env
    from app.core.allocation_reservations import reserve
    from app.core.models import ProxmoxHost
    from app.schemas.v1.allocation import AllocationRequest
    host_id = url.split("/")[-2]
    other_engine = db.build_engine(str(db.get_engine().url))
    factory_a = db.get_session_factory()
    factory_b = db.session_factory(other_engine)
    async with factory_a() as session:
        host = await session.get(ProxmoxHost, host_id)
    try:
        async with AsyncClient(transport=httpx.MockTransport(pve.respond)) as remote:
            results = await asyncio.gather(*[
                reserve(factory, host, AllocationRequest.model_validate(plan(f"engine-{index}")), TOKEN, remote)
                for index, factory in enumerate([factory_a, factory_b])
            ])
        assert {result.assignments[0].vm_id for result in results} == {2000, 2003}
        assert {result.assignments[0].nics[0].ip for result in results} == {"10.42.8.2", "10.42.8.3"}
    finally:
        await other_engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_retries_from_same_owner_return_one_stable_mapping(env):
    client, _, _, url = env
    responses = await asyncio.gather(*[client.post(url, json=plan(), headers=HEADERS) for _ in range(4)])
    assert all(response.status_code == 200 for response in responses)
    assert len({response.json()["reservation_id"] for response in responses}) == 1
    assert len({response.json()["assignments"][0]["vm_id"] for response in responses}) == 1


@pytest.mark.asyncio
async def test_manual_values_are_claimed_before_automatic_values_regardless_of_row_order(env):
    client, _, _, url = env
    request = plan()
    request["vms"].append({"node_id": "manual-last", "vm_id": 2000,
                            "nics": [{"index": 0, "network_id": "lab", "ip": "10.42.8.2"}]})
    response = await client.post(url, json=request, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert [vm["vm_id"] for vm in response.json()["assignments"]] == [2003, 2000]
    assert [vm["nics"][0]["ip"] for vm in response.json()["assignments"]] == ["10.42.8.3", "10.42.8.2"]


@pytest.mark.asyncio
async def test_failed_update_preserves_existing_lease(env):
    client, pve, _, url = env
    first = await client.post(url, json=plan(), headers=HEADERS)
    assert first.status_code == 200, first.text
    pve.occupied.add(2000)
    failed = await client.post(url, json=plan(), headers=HEADERS)
    assert failed.status_code == 409
    assert failed.json()["code"] == "ALLOCATION_OCCUPIED"
    persisted = await client.get(f"{url}/{first.json()['reservation_id']}", headers=HEADERS)
    assert persisted.json() == first.json()


@pytest.mark.asyncio
async def test_registered_host_alias_cannot_bypass_other_projects_lease(env):
    client, _, _, url = env
    first = await client.post(url, json=plan(), headers=HEADERS)
    assert first.status_code == 200, first.text
    alias = await client.post("/v1/proxmox/hosts", json={"name": "same-cluster-alias", "api_url": "https://pve01:8006",
        "node_name": "pve01", "token_ref": "root@pam!alias=fixture"})
    other = await client.post(f"/v1/proxmox/hosts/{alias.json()['id']}/reservations", json=plan("other"), headers=HEADERS)
    assert other.status_code == 200, other.text
    assert other.json()["assignments"][0]["vm_id"] != 2000
    assert other.json()["assignments"][0]["nics"][0]["ip"] != "10.42.8.2"


@pytest.mark.asyncio
async def test_current_and_pending_guest_addresses_both_block_allocation(env, monkeypatch):
    client, pve, _, url = env
    pve.resources = [{"vmid": 5000, "type": "qemu", "node": "pve01"}]
    pve.configs[5000] = {"net0": "virtio=00:11:22:33:44:55,bridge=labnet", "ipconfig0": "ip=10.42.8.2/29"}
    original = pve.respond
    def respond(request):
        if request.url.path.endswith("/config") and request.url.params.get("current") == "1":
            return httpx.Response(200, json={"data": {"net0": "virtio=00:11:22:33:44:55,bridge=labnet", "ipconfig0": "ip=10.42.8.3/29"}})
        return original(request)
    monkeypatch.setattr(pve, "respond", respond)
    response = await client.post(url, json=plan(), headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json()["assignments"][0]["nics"][0]["ip"] == "10.42.8.4"


@pytest.mark.asyncio
async def test_malformed_permissions_return_actionable_error(env, monkeypatch):
    client, pve, _, url = env
    original = pve.respond
    def respond(request):
        if request.url.path.endswith("/permissions"):
            return httpx.Response(200, json={"data": {"/vms": []}})
        return original(request)
    monkeypatch.setattr(pve, "respond", respond)
    response = await client.post(url, json=plan(), headers=HEADERS)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "ALLOCATION_OCCUPANCY_UNAVAILABLE"


@pytest.mark.asyncio
async def test_browser_can_send_reservation_ownership_header(env):
    client, _, _, url = env
    response = await client.options(url, headers={"Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type,x-range42-reservation-token"})
    assert response.status_code == 200, response.text
    assert "x-range42-reservation-token" in response.headers["access-control-allow-headers"].lower()


@pytest.mark.asyncio
async def test_network_probe_does_not_hold_database_writer_lock(env, monkeypatch):
    client, pve, db, url = env
    entered, proceed = asyncio.Event(), asyncio.Event()
    original = pve.respond
    async def respond(request):
        if request.url.path.endswith("/nextid"):
            entered.set()
            await proceed.wait()
        return original(request)
    monkeypatch.setattr(pve, "respond", respond)
    task = asyncio.create_task(client.post(url, json=plan(), headers=HEADERS))
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        async def another_writer():
            async with db.get_session_factory()() as session:
                await session.execute(text("BEGIN IMMEDIATE"))
                await session.execute(text("UPDATE proxmox_hosts SET token_scope = 'audit fixture'"))
                await session.commit()
        await asyncio.wait_for(another_writer(), timeout=1)
    finally:
        proceed.set()
        response = await task
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_subsequent_owner_without_token_cannot_reuse_active_project_key(env):
    client, _, _, url = env
    responses = await asyncio.gather(client.post(url, json=plan(), headers=HEADERS),
        client.post(url, json=plan(), headers={"X-Range42-Reservation-Token": OTHER_TOKEN}))
    assert sorted(response.status_code for response in responses) == [200, 403]


@pytest.mark.asyncio
async def test_fresh_active_guest_address_collision_is_reported_on_renew(env):
    client, pve, _, url = env
    first = await client.post(url, json=plan(), headers=HEADERS)
    assert first.status_code == 200, first.text
    pve.resources = [{"vmid": 5000, "type": "qemu", "node": "pve01"}]
    pve.configs[5000] = {"net0": "virtio=00:11:22:33:44:55,bridge=labnet", "ipconfig0": "ip=10.42.8.2/29"}
    response = await client.post(url, json=plan(), headers=HEADERS)
    assert response.status_code == 409
    assert response.json()["code"] == "ALLOCATION_OCCUPIED"


@pytest.mark.asyncio
async def test_installed_scenarios_reserve_absent_vmids_and_secondary_addresses(env, tmp_path, monkeypatch):
    import json
    from dataclasses import replace
    from app.core import config
    root = tmp_path / "installed"
    scenarios = root / "scenarios"
    scenarios.mkdir(parents=True)
    (scenarios / "_reserved.json").write_text(json.dumps({"vm_id": 2000, "bridge": "labnet", "ip": "10.42.8.2"}) + "\n")
    directory = scenarios / "new" / "manifest"
    directory.mkdir(parents=True)
    (directory / "scenario_vms.json").write_text(json.dumps({"vms": [{"vm_id": 2003,
        "nics": [{"index": 0, "bridge": "other", "ip": "10.42.9.1"},
                 {"index": 1, "bridge": "labnet", "ip": "10.42.8.3"}]}], "templates": []}))
    monkeypatch.setattr(config, "settings", replace(config.settings, wwwapp_playbooks_dir=str(root)))
    client, pve, _, url = env
    response = await client.post(url, json=plan(), headers=HEADERS)
    assert response.status_code == 200, response.text
    assignment = response.json()["assignments"][0]
    assert assignment["vm_id"] == 2004
    assert assignment["nics"][0]["ip"] == "10.42.8.4"
    assert pve.probes == [2004]


@pytest.mark.asyncio
async def test_unavailable_installed_ledger_does_not_create_reservation(env, monkeypatch):
    from dataclasses import replace
    from app.core import config
    monkeypatch.setattr(config, "settings", replace(config.settings, wwwapp_playbooks_dir=""))
    client, pve, db, url = env
    response = await client.post(url, json=plan(), headers=HEADERS)
    assert response.status_code == 409
    assert response.json()["code"] == "ALLOCATION_OCCUPANCY_UNAVAILABLE"
    assert not pve.probes
    async with db.get_engine().connect() as connection:
        assert (await connection.execute(text("SELECT COUNT(*) FROM allocation_reservations"))).scalar_one() == 0
