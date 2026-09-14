"""Host-bound imported guest edits over real HTTP/DB; PVE transport is simulated."""
import copy
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from urllib.parse import parse_qs, quote

import httpx
import pytest
from fastapi import FastAPI

from app.core.auth import BearerAuthMiddleware
from app.core.db import build_engine, session_factory
from app.core.errors import install_exception_handlers
from app.core.models import Base, ProxmoxHost
from app.routes.v1.proxmox import router
from app.routes.v1.proxmox._helpers import _session

AUTH = {"Authorization": "Bearer " + "test-public-api-token" * 2}
SECRET = "test@pve!edit=do-not-return-this-secret"
BASE = "/v1/proxmox/hosts/selected/vms/60001/config"


class Pve:
    def __init__(self):
        self.node = "pve-b"
        self.expected_token = SECRET
        self.config = {"digest": "a" * 40, "name": "guest", "hostname": "guest",
                       "cores": 2, "memory": 2048, "description": "Imported guest",
                       "tags": "old", "cipassword": SECRET}
        self.current = copy.deepcopy(self.config)
        self.calls = []
        self.response = None
        self.write_status = 200
        self.read_status = 200
        self.after_write = None
        self.before_read = None
        self.apply = True
        self.upid = "UPID:pve-b:00000001:00000002:00000003:qmconfig:60001:user@pam:"
        self.external_edit = False

    async def respond(self, request):
        self.calls.append(request)
        assert request.url.host == "selected.test"
        assert "/nodes/" + self.node + "/" in request.url.path
        assert request.headers["Authorization"] == "PVEAPIToken=" + self.expected_token
        if "/tasks/" in request.url.path:
            return httpx.Response(200, json={"data": {"status": "stopped", "exitstatus": "OK"}})
        if "/snapshot" in request.url.path:
            return httpx.Response(200, json={"data": "UPID:pve-b:snapshot"})
        if request.method == "GET":
            if self.before_read:
                await self.before_read()
                self.before_read = None
            if self.read_status != 200:
                return httpx.Response(self.read_status, text=SECRET)
            return httpx.Response(200, json={"data": self.current if request.url.params.get("current") == "1" else self.config})
        if self.write_status != 200:
            return httpx.Response(self.write_status, text="detected modified configuration " + SECRET)
        data = {k: v[0] for k, v in parse_qs(request.content.decode(), keep_blank_values=True).items()}
        if self.external_edit:
            self.config["digest"] = "d" * 40
        if data.pop("digest") != self.config["digest"]:
            return httpx.Response(500, text="checksum mismatch " + SECRET)
        if self.apply:
            self.config.update(data)
            for key in ("cores", "memory"):
                self.config[key] = int(self.config[key])
            self.config["digest"] = "b" * 40
            self.current["digest"] = "b" * 40
        if self.after_write:
            self.after_write(self)
        return httpx.Response(200, json={"data": self.upid if request.method == "POST" else self.response})

    @property
    def writes(self):
        return [r for r in self.calls if r.method != "GET"]


@asynccontextmanager
async def api(tmp_path, monkeypatch):
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    factory = session_factory(engine)
    async with engine.begin() as con:
        await con.run_sync(Base.metadata.create_all)
    async with factory() as session:
        for id_, url, node in (("first", "other.test", "pve-a"), ("selected", "selected.test", "pve-b")):
            session.add(ProxmoxHost(id=id_, name=id_, api_url=f"https://{url}:8006/", node_name=node, token_ref=SECRET))
        await session.commit()
    app = FastAPI()
    install_exception_handlers(app)
    app.add_middleware(BearerAuthMiddleware, token=AUTH["Authorization"][7:])
    app.include_router(router, prefix="/v1")

    async def session_dependency():
        async with factory() as session:
            yield session
    app.dependency_overrides[_session] = session_dependency
    pve = Pve()
    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original_client(transport=httpx.MockTransport(pve.respond), **kw))
    monkeypatch.setenv("RANGE42_WORKSPACE_ROOT", str(tmp_path))
    try:
        async with original_client(transport=httpx.ASGITransport(app=app), base_url="http://api", headers=AUTH) as client:
            yield client, pve, factory
    finally:
        await engine.dispose()


async def review(client, base=BASE):
    result = await client.get(base + "/review")
    assert result.status_code == 200, result.text
    return result.json()


@pytest.mark.asyncio
async def test_review_binds_selected_host_and_exposes_only_current_configured_fields(tmp_path, monkeypatch):
    async with api(tmp_path, monkeypatch) as (client, pve, _):
        pve.config["memory"] = "current=4096"
        result = await review(client)
        assert (result["host_id"], result["node"], result["vmid"], result["vmtype"]) == ("selected", "pve-b", 60001, "qemu")
        assert len(result["digest"]) == 64
        assert result["current"]["memory"] == 2048
        assert result["configured"]["memory"] == 4096
        assert result["pending"] == ["memory"]
        assert set(result["configured"]) == {"name", "description", "cores", "memory", "tags"}
        assert SECRET not in str(result)


@pytest.mark.asyncio
async def test_sync_null_metadata_write_requires_verified_readback(tmp_path, monkeypatch):
    async with api(tmp_path, monkeypatch) as (client, pve, _):
        before = await review(client)
        result = await client.put(BASE, json={"digest": before["digest"], "changes": {"name": "renamed", "tags": ""}})
        assert result.status_code == 200, result.text
        body = result.json()
        assert body["status"] == "configured" and body["upid"] is None
        assert body["review"]["configured"]["name"] == "renamed"
        assert body["review"]["current"]["name"] == "guest"
        assert body["review"]["pending"] == ["name", "tags"]
        assert len(pve.writes) == 1 and pve.writes[0].method == "PUT"
        assert pve.config["cipassword"] == SECRET


@pytest.mark.asyncio
async def test_qemu_resource_write_returns_original_node_task_without_claiming_applied(tmp_path, monkeypatch):
    async with api(tmp_path, monkeypatch) as (client, pve, _):
        before = await review(client)
        result = await client.put(BASE, json={"digest": before["digest"], "changes": {"memory": 4096}})
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "accepted"
        assert result.json()["upid"] == pve.upid
        assert result.json()["review"] is None
        assert pve.writes[0].method == "POST"


@pytest.mark.asyncio
async def test_qemu_async_api_immediate_null_completion_requires_readback(tmp_path, monkeypatch):
    async with api(tmp_path, monkeypatch) as (client, pve, _):
        before = await review(client)
        pve.upid = None
        result = await client.put(BASE, json={"digest": before["digest"], "changes": {"memory": 4096}})
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "configured"
        assert result.json()["review"]["configured"]["memory"] == 4096
        assert result.json()["review"]["pending"] == ["memory"]


@pytest.mark.asyncio
async def test_lxc_name_maps_hostname_and_resources_are_synchronous(tmp_path, monkeypatch):
    async with api(tmp_path, monkeypatch) as (client, pve, _):
        before = (await client.get(BASE + "/review?vmtype=lxc")).json()
        result = await client.put(BASE + "?vmtype=lxc", json={"digest": before.get("digest", "a" * 64), "changes": {"name": "container", "cores": 4}})
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "configured"
        assert result.json()["review"]["configured"]["name"] == "container"
        assert "hostname=container" in pve.writes[0].content.decode()
        assert pve.writes[0].method == "PUT"


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{}, {"net0": "evil"}, {"cores": True}, {"cores": "2"}, {"cores": 0}, {"memory": 15}, {"name": "bad/name"}, {"description": "range42-deployment:fake"}, {"tags": "a;a"}, {"tags": "x;y secret"}, {"tags": None}])
async def test_invalid_changes_fail_before_pve(tmp_path, monkeypatch, changes):
    async with api(tmp_path, monkeypatch) as (client, pve, _):
        result = await client.put(BASE, json={"digest": "a" * 64, "changes": changes})
        assert result.status_code == 422, result.text
        assert not pve.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["digest", "host", "node", "type"])
async def test_stale_review_or_target_change_never_writes(tmp_path, monkeypatch, kind):
    async with api(tmp_path, monkeypatch) as (client, pve, factory):
        before = await review(client)
        if kind == "digest":
            pve.config["digest"] = pve.current["digest"] = "c" * 40
        elif kind in {"host", "node"}:
            async with factory() as session:
                row = await session.get(ProxmoxHost, "selected")
                if kind == "host":
                    row.api_url = "https://selected.test:8007/"
                else:
                    row.node_name = "pve-c"
                await session.commit()
            if kind == "node":
                pve.node = "pve-c"
        result = await client.put(BASE + ("?vmtype=lxc" if kind == "type" else ""), json={"digest": before["digest"], "changes": {"tags": "new"}})
        assert result.status_code == 409, result.text
        assert not pve.writes


@pytest.mark.asyncio
@pytest.mark.parametrize("config,code", [({"template": 1}, "VM_CONFIG_TEMPLATE"), ({"lock": "backup"}, "VM_CONFIG_LOCKED"), ({"description": "range42-deployment:abc"}, "VM_CONFIG_MANAGED")])
async def test_nonimported_guest_guards(tmp_path, monkeypatch, config, code):
    async with api(tmp_path, monkeypatch) as (client, pve, _):
        pve.config.update(config)
        pve.current.update(config)
        result = await client.put(BASE, json={"digest": "a" * 64, "changes": {"tags": "new"}})
        assert result.status_code == 409
        assert result.json()["code"] == code
        assert not pve.writes


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing", "mismatch", "malformed", "foreign_task", "write_timeout", "upstream_failure"])
async def test_after_dispatch_uncertainty_never_claims_success_or_retry_safe_error(tmp_path, monkeypatch, mode):
    async with api(tmp_path, monkeypatch) as (client, pve, _):
        before = await review(client)
        changes = {"tags": "new"}
        if mode == "missing":
            pve.after_write = lambda p: setattr(p, "read_status", 403)
        elif mode == "mismatch":
            pve.apply = False
        elif mode == "malformed":
            pve.response = {}
        elif mode == "foreign_task":
            changes = {"memory": 4096}
            pve.upid = "UPID:pve-a:1:2:3:qmconfig:60001:user@pam:"
        elif mode == "write_timeout":
            def timeout(p):
                raise httpx.ReadTimeout(SECRET)
            pve.after_write = timeout
        else:
            pve.write_status = 500
        result = await client.put(BASE, json={"digest": before["digest"], "changes": changes})
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "unconfirmed"
        assert result.json()["reason"]
        assert SECRET not in result.text
        assert len(pve.writes) == 1


@pytest.mark.asyncio
async def test_authentication_and_protected_ids_refuse_before_pve(tmp_path, monkeypatch):
    async with api(tmp_path, monkeypatch) as (client, pve, _):
        for method, url in (("GET", BASE + "/review"), ("PUT", BASE)):
            r = await client.request(method, url, headers={"Authorization": ""})
            assert r.status_code == 401
        r = await client.put(BASE.replace("60001", "100"), json={"digest": "a" * 64, "changes": {"tags": "new"}})
        assert r.status_code == 409 and r.json()["code"] == "VMID_PROTECTED"
        assert not pve.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "DELETE"])
@pytest.mark.parametrize("vmtype", ["qemu", "lxc"])
async def test_protected_snapshot_creation_and_deletion_refuse_before_pve(tmp_path, monkeypatch, method, vmtype):
    async with api(tmp_path, monkeypatch) as (client, pve, _):
        url = "/v1/proxmox/hosts/selected/vms/100/snapshots"
        result = await client.request(method, url + ("/safe" if method == "DELETE" else "") + "?vmtype=" + vmtype,
                                      json={"snapname": "safe"} if method == "POST" else None)
        assert result.status_code == 409, result.text
        assert result.json()["code"] == "VMID_PROTECTED"
        assert not pve.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["digest", "skew", "shape", "permission", "network", "numeric"])
async def test_incomplete_or_denied_review_fails_closed_and_redacts_upstream(tmp_path, monkeypatch, mode):
    async with api(tmp_path, monkeypatch) as (client, pve, _):
        if mode == "digest":
            del pve.config["digest"]
        elif mode == "skew":
            pve.current["digest"] = "d" * 40
        elif mode == "shape":
            pve.config = [SECRET]
        elif mode == "permission":
            pve.read_status = 403
        elif mode == "network":
            async def fail():
                raise httpx.ConnectError(SECRET)
            pve.before_read = fail
        else:
            pve.config["memory"] = {"arbitrary": SECRET}
        result = await client.put(BASE, json={"digest": "a" * 64, "changes": {"tags": "new"}})
        assert result.status_code in (403, 409, 502)
        assert SECRET not in result.text
        assert not pve.writes


@pytest.mark.asyncio
async def test_upstream_digest_enforcement_prevents_intervening_external_edit(tmp_path, monkeypatch):
    async with api(tmp_path, monkeypatch) as (client, pve, _):
        before = await review(client)
        pve.external_edit = True
        result = await client.put(BASE, json={"digest": before["digest"], "changes": {"tags": "new"}})
        assert result.json()["status"] == "unconfirmed"
        assert pve.config["tags"] == "old"
        assert len(pve.writes) == 1


@pytest.mark.asyncio
async def test_claim_without_guest_marker_still_blocks_imported_editor(tmp_path, monkeypatch):
    from app.core.allocation_models import DeploymentAllocation
    from app.core.models import Source, Project, Deployment
    async with api(tmp_path, monkeypatch) as (client, pve, factory):
        async with factory() as session:
            session.add(Source(id="source", provider="github", base_url="https://github.com", auth_kind="none"))
            await session.flush()
            session.add(Project(id="project", name="project", source_id="source", branch_strategy="fixed"))
            await session.flush()
            session.add(Deployment(id="deployment", codename="owned", scenario_label="test", project_id="project", target_host_id="selected", workspace_path=str(tmp_path)))
            await session.flush()
            session.add(DeploymentAllocation(id="claim", deployment_id="deployment", project_sha="e" * 40,
                host_id="selected", api_url="https://selected.test:8006", node_name="pve-b",
                assignments=[{"vm_id": 60001, "nics": []}], created_at=datetime.now(timezone.utc)))
            await session.commit()
        result = await client.put(BASE, json={"digest": "a" * 64, "changes": {"description": "replace ownership"}})
        assert result.status_code == 409 and result.json()["code"] == "VM_CONFIG_MANAGED"
        assert not pve.calls


@pytest.mark.asyncio
async def test_provisioning_lock_blocks_config_write_and_is_released_after_failure(tmp_path, monkeypatch):
    from app.core.locks import ProvisioningLock
    async with api(tmp_path, monkeypatch) as (client, pve, _):
        with ProvisioningLock(tmp_path / ".locks"):
            result = await client.put(BASE, json={"digest": "a" * 64, "changes": {"tags": "new"}})
            assert result.status_code == 409 and result.json()["code"] == "PROVISIONING_BUSY"
            assert not pve.calls
        before = await review(client)
        pve.write_status = 500
        result = await client.put(BASE, json={"digest": before["digest"], "changes": {"tags": "new"}})
        assert result.json()["status"] == "unconfirmed"
        with ProvisioningLock(tmp_path / ".locks"):
            pass


@pytest.mark.asyncio
async def test_host_binding_writer_waits_until_checked_dispatch_finishes(tmp_path, monkeypatch):
    async with api(tmp_path, monkeypatch) as (client, pve, factory):
        before = await review(client)
        reading, release, entered, changed = (asyncio.Event() for _ in range(4))

        async def hold_read():
            reading.set()
            await release.wait()

        async def rebind():
            async with factory() as session:
                row = await session.get(ProxmoxHost, "selected")
                row.node_name = "elsewhere"
                entered.set()
                await session.commit()
            changed.set()

        pve.before_read = hold_read
        writer = asyncio.create_task(client.put(BASE, json={"digest": before["digest"], "changes": {"tags": "new"}}))
        await asyncio.wait_for(reading.wait(), 2)
        rebind_task = asyncio.create_task(rebind())
        try:
            await asyncio.wait_for(entered.wait(), 2)
            await asyncio.sleep(0.05)
            assert not changed.is_set()
        finally:
            release.set()
            result = await asyncio.wait_for(writer, 3)
            await asyncio.wait_for(rebind_task, 3)
        assert result.json()["status"] == "configured"
        assert changed.is_set()
        assert all("/nodes/pve-b/" in r.url.path for r in pve.writes)


def config_task_url(upid, target_digest):
    return f"/v1/proxmox/hosts/selected/tasks/{quote(upid, safe='')}/status?expected_target_digest={target_digest}"


@pytest.mark.asyncio
async def test_static_target_digest_survives_normal_configuration_change_and_guarded_poll(tmp_path, monkeypatch):
    async with api(tmp_path, monkeypatch) as (client, pve, _):
        before = await review(client)
        assert len(before.get("target_digest", "")) == 64
        accepted = await client.put(BASE, json={"digest": before["digest"], "changes": {"memory": 4096}})
        assert accepted.json()["status"] == "accepted"
        task = await client.get(config_task_url(accepted.json()["upid"], before["target_digest"]))
        assert task.status_code == 200 and task.json()["exitstatus"] == "OK"
        after = await review(client)
        assert after["digest"] != before["digest"]
        assert after["target_digest"] == before["target_digest"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["api_url", "node", "credential", "protection"])
async def test_binding_change_after_accepted_task_refuses_poll_before_contact_and_changes_fresh_review(tmp_path, monkeypatch, change):
    async with api(tmp_path, monkeypatch) as (client, pve, factory):
        before = await review(client)
        accepted = await client.put(BASE, json={"digest": before["digest"], "changes": {"memory": 4096}})
        assert accepted.json()["status"] == "accepted"
        async with factory() as session:
            row = await session.get(ProxmoxHost, "selected")
            if change == "api_url":
                row.api_url = "https://selected.test:8007/"
            elif change == "node":
                row.node_name = pve.node = "pve-c"
            elif change == "credential":
                row.token_ref = pve.expected_token = "test@pve!rotated=other-public-test-token"
            else:
                row.protected_vmids_override_json = "[[80000,80001]]"
            await session.commit()
        calls_before = len(pve.calls)
        polled = await client.get(config_task_url(accepted.json()["upid"], before.get("target_digest", "a" * 64)))
        assert polled.status_code == 409, polled.text
        assert polled.json()["code"] == "VM_CONFIG_TARGET_CHANGED"
        assert len(pve.calls) == calls_before
        after = await review(client)
        assert after["target_digest"] != before["target_digest"]
        assert SECRET not in polled.text and pve.expected_token not in str(after)


@pytest.mark.asyncio
@pytest.mark.parametrize("other", ["type", "vmid", "task_kind", "malformed_digest"])
async def test_guarded_poll_refuses_wrong_guest_or_type_and_unrelated_tasks(tmp_path, monkeypatch, other):
    async with api(tmp_path, monkeypatch) as (client, pve, _):
        before = await review(client)
        target_digest = before.get("target_digest", "a" * 64)
        upid = pve.upid
        if other == "type":
            container = (await client.get(BASE + "/review?vmtype=lxc")).json()
            target_digest = container.get("target_digest", "b" * 64)
        elif other == "vmid":
            upid = upid.replace(":60001:", ":60002:")
        elif other == "task_kind":
            upid = upid.replace(":qmconfig:", ":qmstart:")
        else:
            target_digest = "not-a-digest"
        calls_before = len(pve.calls)
        result = await client.get(config_task_url(upid, target_digest))
        assert result.status_code in (409, 422), result.text
        assert len(pve.calls) == calls_before
