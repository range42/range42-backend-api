"""Durable, explicitly reviewed scenario snapshots use only owned QEMU targets."""

import json
import re
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.models import Deployment
from tests.routes.test_project_scenario_execution import _boot, seed_scenario


class Proxmox:
    def __init__(self):
        self.configs = {
            vmid: {
                "name": f"guest-{vmid}",
                "description": "range42-deployment:dep-1",
                "smbios1": f"uuid=00000000-0000-4000-8000-{vmid:012d}",
                "scsi0": f"local-zfs:vm-{vmid}-disk-0,size=16G",
                "cores": 2,
                "memory": 2048,
                "digest": "a" * 40,
            }
            for vmid in [5000, 5001]
        }
        self.snapshots = {vmid: {} for vmid in self.configs}
        self.tasks = {}
        self.writes = []
        self.fail_vmid = None
        self.ambiguous_vmid = None

    def handle(self, request):
        path = unquote(request.url.path).removeprefix("/api2/json")
        if path == "/cluster/resources":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "vmid": vmid,
                            "type": "qemu",
                            "node": "pve01",
                            "name": config["name"],
                            "template": 0,
                            "status": "stopped",
                        }
                        for vmid, config in self.configs.items()
                    ]
                },
            )
        if "/tasks/" in path:
            upid = path.split("/tasks/")[1].removesuffix("/status")
            return httpx.Response(200, json={"data": self.tasks[upid]})
        match = re.fullmatch(r"/nodes/pve01/qemu/(\d+)/(.*)", path)
        assert match, path
        vmid, tail = int(match[1]), match[2]
        if request.method == "GET":
            if tail == "config":
                data = self.configs[vmid]
            elif tail == "status/current":
                data = {"status": "stopped"}
            elif tail == "pending":
                data = []
            elif tail == "snapshot":
                data = [
                    {
                        "name": name,
                        "description": row["description"],
                        "snaptime": row["snaptime"],
                    }
                    for name, row in self.snapshots[vmid].items()
                ]
            elif tail.endswith("/config"):
                data = self.snapshots[vmid][tail.split("/")[1]]
            else:
                raise AssertionError(path)
            return httpx.Response(200, json={"data": deepcopy(data)})
        from urllib.parse import parse_qs

        body = {
            key: values[0] for key, values in parse_qs(request.content.decode()).items()
        }
        self.writes.append((request.method, path, body))
        if getattr(self, "after_write", None):
            self.after_write()
        name = body.get("snapname") or tail.split("/")[1]
        kind = (
            "qmsnapshot"
            if tail == "snapshot"
            else "qmrollback"
            if tail.endswith("rollback")
            else "qmdelsnapshot"
        )
        upid = f"UPID:pve01:00001:00002:00003:{kind}:{vmid}:deployer@pve!ui:"
        if self.ambiguous_vmid == vmid:
            raise httpx.ReadTimeout("private upstream failure")
        outcome = "failure" if self.fail_vmid == vmid else "OK"
        if outcome == "OK":
            if kind == "qmsnapshot":
                self.snapshots[vmid][name] = {
                    **self.configs[vmid],
                    "description": body["description"],
                    "snaptime": int(datetime.now(timezone.utc).timestamp()),
                }
                self.configs[vmid]["parent"] = name
            elif kind == "qmdelsnapshot":
                del self.snapshots[vmid][name]
            else:
                marker = self.configs[vmid]["description"]
                self.configs[vmid] = {
                    **self.snapshots[vmid][name],
                    "description": marker,
                    "parent": name,
                }
        self.tasks[upid] = {"upid": upid, "status": "running", "_outcome": outcome}
        return httpx.Response(200, json={"data": upid})

    def finish(self):
        for row in self.tasks.values():
            row.update(
                status="stopped",
                exitstatus=row.pop("_outcome", row.get("exitstatus", "OK")),
            )


@pytest.fixture
async def snapshots(tmp_path, monkeypatch):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    manifest = {
        "version": 1,
        "scenario": "content",
        "vms": [{"vm_id": vmid, "vm_name": f"guest-{vmid}"} for vmid in [5000, 5001]],
    }
    await seed_scenario(
        dbmod,
        tmp_path,
        extra_files={"manifest/scenario_vms.json": json.dumps(manifest)},
    )
    from app.core.models import ProxmoxHost

    async with dbmod.get_session_factory()() as session:
        dep = await session.get(Deployment, "dep-1")
        dep.state = "succeeded"
        host = await session.get(ProxmoxHost, "h")
        host.node_name = "pve01"
        await session.commit()
    pve = Proxmox()
    original = httpx.AsyncClient
    async with original(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as client:
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda **kwargs: original(
                **{**kwargs, "transport": httpx.MockTransport(pve.handle)}
            ),
        )
        try:
            yield client, dbmod, pve
        finally:
            await dbmod.dispose_engine()


async def plan(client):
    response = await client.post(
        "/v1/deployments/dep-1/snapshot-sets/plan",
        json={"name": "Before exercise", "description": "Reviewed checkpoint"},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def execute(client, item):
    return await client.post(
        f"/v1/deployments/dep-1/snapshot-sets/{item['id']}/execute",
        json={"plan_digest": item["operation"]["plan_digest"]},
    )


async def reconcile(client, item):
    return await client.post(
        f"/v1/deployments/dep-1/snapshot-sets/{item['id']}/reconcile"
    )


@pytest.mark.asyncio
async def test_plan_is_read_only_and_execute_requires_exact_review(snapshots):
    client, _dbmod, pve = snapshots
    item = await plan(client)
    assert len(item["operation"]["members"]) == 2
    assert item["atomic"] is False and item["state"] == "planned"
    assert pve.writes == []
    wrong = deepcopy(item)
    wrong["operation"]["plan_digest"] = "0" * 64
    assert (await execute(client, wrong)).status_code == 409
    assert pve.writes == []


@pytest.mark.asyncio
async def test_native_jobs_survive_reconcile_without_duplicate_dispatch(snapshots):
    client, dbmod, pve = snapshots
    item = await plan(client)
    response = await execute(client, item)
    assert response.status_code == 202, response.text
    assert len(pve.writes) == 2
    assert response.json()["state"] == "creating"
    assert (await execute(client, item)).status_code == 409
    assert (await reconcile(client, item)).json()["state"] == "creating"
    pve.finish()
    result = (await reconcile(client, item)).json()
    assert result["state"] == "complete"
    assert all(row["state"] == "succeeded" for row in result["operation"]["members"])
    assert len(pve.writes) == 2
    from app.core.models import Attempt, WorkspaceLock

    async with dbmod.get_session_factory()() as session:
        attempt = await session.get(Attempt, result["operation"]["attempt_id"])
        assert attempt.state == "succeeded"
        assert await session.get(WorkspaceLock, "dep-1") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed", ["foreign", "template", "different_uuid", "different_config"]
)
async def test_stale_or_foreign_member_refuses_whole_set_before_any_write(
    snapshots, changed
):
    client, _dbmod, pve = snapshots
    item = await plan(client)
    if changed == "foreign":
        pve.configs[5001]["description"] = "another owner"
    elif changed == "template":
        pve.configs[5001]["template"] = 1
    elif changed == "different_uuid":
        pve.configs[5001]["smbios1"] = "uuid=11111111-1111-4111-8111-111111111111"
    else:
        pve.configs[5001]["memory"] = 4096
    assert (await execute(client, item)).status_code == 409
    assert pve.writes == []


@pytest.mark.asyncio
async def test_partial_native_failure_is_explicit_and_never_automatically_rolled_back(
    snapshots,
):
    client, _dbmod, pve = snapshots
    item = await plan(client)
    pve.fail_vmid = 5001
    assert (await execute(client, item)).status_code == 202
    pve.finish()
    result = (await reconcile(client, item)).json()
    assert result["state"] == "partial"
    assert [row["state"] for row in result["operation"]["members"]] == [
        "succeeded",
        "failed",
    ]
    assert len(pve.writes) == 2
    assert pve.snapshots[5000]


@pytest.mark.asyncio
async def test_ambiguous_dispatch_retains_active_lock_and_cannot_cancel_or_reissue(
    snapshots,
):
    client, dbmod, pve = snapshots
    item = await plan(client)
    pve.ambiguous_vmid = 5000
    response = await execute(client, item)
    assert response.status_code == 202
    assert response.json()["state"] == "needs_review"
    assert len(pve.writes) == 1
    assert (await execute(client, item)).status_code == 409
    assert (await client.post("/v1/deployments/dep-1/cancel")).status_code == 409
    from app.core.locks import cleanup_stale_locks
    from app.core.models import WorkspaceLock

    async with dbmod.get_session_factory()() as session:
        lock = await session.get(WorkspaceLock, "dep-1")
        lock.heartbeat_at = datetime.now(timezone.utc) - timedelta(days=1)
        await session.commit()
        assert await cleanup_stale_locks(session) == 0
    assert len(pve.writes) == 1


@pytest.mark.asyncio
async def test_member_changed_after_first_dispatch_is_not_written(snapshots):
    client, _dbmod, pve = snapshots
    item = await plan(client)
    pve.after_write = lambda: pve.configs[5001].update(description="different owner")
    response = await execute(client, item)
    assert response.status_code == 202
    assert len(pve.writes) == 1
    assert response.json()["operation"]["members"][1]["state"] == "not_started"


@pytest.mark.asyncio
async def test_restart_reconciles_existing_upids_and_keeps_active_lock(snapshots):
    client, dbmod, pve = snapshots
    item = await plan(client)
    assert (await execute(client, item)).status_code == 202
    from app.core.orphans import reconcile_once
    from app.core.models import WorkspaceLock

    await reconcile_once()
    assert len(pve.writes) == 2
    async with dbmod.get_session_factory()() as session:
        assert await session.get(WorkspaceLock, "dep-1") is not None
    pve.finish()
    await reconcile_once()
    result = (
        await client.get(f"/v1/deployments/dep-1/snapshot-sets/{item['id']}")
    ).json()
    assert result["state"] == "complete"
    assert len(pve.writes) == 2


@pytest.mark.asyncio
async def test_rebound_host_refuses_polling_original_task_on_new_host(snapshots):
    client, dbmod, pve = snapshots
    item = await plan(client)
    assert (await execute(client, item)).status_code == 202
    from app.core.models import ProxmoxHost

    async with dbmod.get_session_factory()() as session:
        host = await session.get(ProxmoxHost, "h")
        host.token_ref = "deployer@pve!ui=replaced"
        await session.commit()
    pve.finish()
    response = await reconcile(client, item)
    assert response.status_code == 409
    assert response.json()["code"] == "SNAPSHOT_TARGET_CHANGED"
    assert len(pve.writes) == 2


@pytest.mark.asyncio
async def test_rollback_has_a_fresh_review_and_confirms_each_stopped_target(snapshots):
    client, _dbmod, pve = snapshots
    item = await plan(client)
    assert (await execute(client, item)).status_code == 202
    pve.finish()
    assert (await reconcile(client, item)).json()["state"] == "complete"
    pve.configs[5000]["memory"] = 4096
    reviewed = await client.post(
        f"/v1/deployments/dep-1/snapshot-sets/{item['id']}/rollback/plan"
    )
    assert reviewed.status_code == 201
    assert len(pve.writes) == 2
    assert (await execute(client, reviewed.json())).status_code == 202
    assert len(pve.writes) == 4
    pve.finish()
    result = (await reconcile(client, item)).json()
    assert result["operation"]["kind"] == "rollback"
    assert result["operation"]["state"] == "succeeded"
    assert pve.configs[5000]["memory"] == 2048


@pytest.mark.asyncio
async def test_owned_set_delete_is_reviewed_and_never_removes_unrelated_snapshots(
    snapshots,
):
    client, _dbmod, pve = snapshots
    item = await plan(client)
    assert (await execute(client, item)).status_code == 202
    pve.finish()
    await reconcile(client, item)
    pve.snapshots[5000]["operator-manual"] = {
        "description": "someone else",
        "snaptime": 1,
    }
    reviewed = await client.post(
        f"/v1/deployments/dep-1/snapshot-sets/{item['id']}/delete/plan"
    )
    assert reviewed.status_code == 201
    assert (await execute(client, reviewed.json())).status_code == 202
    pve.finish()
    result = (await reconcile(client, item)).json()
    assert result["state"] == "deleted"
    assert list(pve.snapshots[5000]) == ["operator-manual"]


@pytest.mark.asyncio
async def test_cancel_unexecuted_plan_never_dispatches_or_changes_guest(snapshots):
    client, _dbmod, pve = snapshots
    item = await plan(client)
    response = await client.delete(
        f"/v1/deployments/dep-1/snapshot-sets/{item['id']}/plans/{item['operation']['id']}"
    )
    assert response.status_code == 204
    assert (await execute(client, item)).status_code == 409
    assert pve.writes == []


async def complete(client, pve):
    item = await plan(client)
    assert (await execute(client, item)).status_code == 202
    pve.finish()
    result = await reconcile(client, item)
    assert result.json()["state"] == "complete", result.text
    return result.json()


@pytest.mark.asyncio
async def test_retention_reviews_only_old_complete_sets_and_policy_change_refuses_delete(
    snapshots,
):
    client, dbmod, pve = snapshots
    old = await complete(client, pve)
    await complete(client, pve)
    from app.core.snapshot_models import SnapshotSet

    async with dbmod.get_session_factory()() as session:
        item = await session.get(SnapshotSet, old["id"])
        item.created_at = datetime.now(timezone.utc) - timedelta(days=30)
        await session.commit()
    policy = await client.put(
        "/v1/admin/retention", json={"keep_count": 1, "keep_days": 7}
    )
    assert policy.json()["automatic_enforcement"] is False
    response = await client.post("/v1/deployments/dep-1/snapshot-sets/retention/plan")
    assert response.status_code == 201, response.text
    assert [row["id"] for row in response.json()["candidates"]] == [old["id"]]
    assert len(pve.writes) == 4
    await client.put("/v1/admin/retention", json={"keep_count": 2, "keep_days": 7})
    assert (await execute(client, response.json()["candidates"][0])).status_code == 409
    assert len(pve.writes) == 4


@pytest.mark.asyncio
async def test_retention_delete_requires_explicit_execution_and_excludes_partial(
    snapshots,
):
    client, _dbmod, pve = snapshots
    old = await complete(client, pve)
    partial = await plan(client)
    pve.fail_vmid = 5001
    await execute(client, partial)
    pve.finish()
    assert (await reconcile(client, partial)).json()["state"] == "partial"
    pve.fail_vmid = None
    await client.put("/v1/admin/retention", json={"keep_count": 0, "keep_days": 0})
    review = await client.post("/v1/deployments/dep-1/snapshot-sets/retention/plan")
    assert review.status_code == 201
    assert [row["id"] for row in review.json()["candidates"]] == [old["id"]]
    assert len(pve.writes) == 4
    assert (await execute(client, review.json()["candidates"][0])).status_code == 202
    pve.finish()
    assert (await reconcile(client, old)).json()["state"] == "deleted"
    assert partial["native_name"] in pve.snapshots[5000]


@pytest.mark.asyncio
async def test_list_snapshots_is_bounded_and_metadata_only(snapshots):
    client, _dbmod, pve = snapshots
    item = await plan(client)
    response = await client.get("/v1/deployments/dep-1/snapshot-sets?limit=1&offset=0")
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == item["id"]
    assert (
        await client.get("/v1/deployments/dep-1/snapshot-sets?offset=-1")
    ).status_code == 422
    assert pve.writes == []


@pytest.mark.asyncio
async def test_expired_plan_refuses_and_foreign_snapshot_cannot_be_deleted(snapshots):
    client, dbmod, pve = snapshots
    item = await plan(client)
    from app.core.snapshot_models import SnapshotOperation

    async with dbmod.get_session_factory()() as session:
        operation = await session.get(SnapshotOperation, item["operation"]["id"])
        operation.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()
    assert (await execute(client, item)).status_code == 409
    completed = await complete(client, pve)
    pve.snapshots[5001][completed["native_name"]]["description"] = "other owner"
    response = await client.post(
        f"/v1/deployments/dep-1/snapshot-sets/{completed['id']}/delete/plan"
    )
    assert response.status_code == 409
    assert len(pve.writes) == 2


@pytest.mark.asyncio
async def test_readback_must_match_saved_snapshot_not_merely_any_current_snapshot(
    snapshots,
):
    client, _dbmod, pve = snapshots
    item = await complete(client, pve)
    reviewed = await client.post(
        f"/v1/deployments/dep-1/snapshot-sets/{item['id']}/rollback/plan"
    )
    assert (await execute(client, reviewed.json())).status_code == 202
    pve.finish()
    for vmid in pve.configs:
        pve.configs[vmid]["memory"] = 8192
        pve.snapshots[vmid][item["native_name"]]["memory"] = 8192
    result = (await reconcile(client, item)).json()
    assert result["operation"]["state"] == "running"
    assert all(
        row["code"] == "SNAPSHOT_READBACK_UNCONFIRMED"
        for row in result["operation"]["members"]
    )


@pytest.mark.asyncio
async def test_snapshot_readback_matches_real_pve_omission_of_unused_disks(snapshots):
    client, _dbmod, pve = snapshots
    pve.configs[5000]["unused0"] = "local-zfs:vm-5000-disk-5"
    item = await plan(client)
    await execute(client, item)
    pve.finish()
    pve.snapshots[5000][item["native_name"]].pop("unused0")
    assert (await reconcile(client, item)).json()["state"] == "complete"


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot_has_password", [True, False])
async def test_snapshot_readback_handles_masked_cloud_init_password(
    snapshots, snapshot_has_password
):
    client, dbmod, pve = snapshots
    # PVE masks this field in current config, but snapshot config returns its hash.
    pve.configs[5000]["cipassword"] = "**********"
    item = await plan(client)
    assert (await execute(client, item)).status_code == 202
    saved = pve.snapshots[5000][item["native_name"]]
    if snapshot_has_password:
        saved["cipassword"] = "$5$test-salt$test-password-hash"
    else:
        saved.pop("cipassword")
    pve.finish()

    response = await reconcile(client, item)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["state"] == ("complete" if snapshot_has_password else "creating")
    assert len(pve.writes) == 2
    from app.core.models import Attempt, WorkspaceLock

    async with dbmod.get_session_factory()() as session:
        attempt = await session.get(Attempt, result["operation"]["attempt_id"])
        lock = await session.get(WorkspaceLock, "dep-1")
        if snapshot_has_password:
            assert attempt.state == "succeeded"
            assert lock is None
        else:
            assert attempt.state == "deploying"
            assert lock is not None
            assert result["operation"]["members"][0]["code"] == (
                "SNAPSHOT_READBACK_UNCONFIRMED"
            )


@pytest.mark.asyncio
async def test_crash_after_persisted_intent_never_reissues_and_keeps_lock(
    snapshots, monkeypatch
):
    import asyncio
    from app.core import snapshot_sets

    client, dbmod, pve = snapshots
    item = await plan(client)

    async def lost_request(*args):
        raise asyncio.CancelledError()

    monkeypatch.setattr(snapshot_sets, "_dispatch", lost_request)
    with pytest.raises(asyncio.CancelledError):
        await snapshot_sets.execute(
            "dep-1", item["id"], item["operation"]["plan_digest"]
        )
    result = (await reconcile(client, item)).json()
    assert result["operation"]["state"] == "needs_review"
    assert [row["state"] for row in result["operation"]["members"]] == [
        "unconfirmed",
        "not_started",
    ]
    assert pve.writes == []
    from app.core.models import WorkspaceLock

    async with dbmod.get_session_factory()() as session:
        assert await session.get(WorkspaceLock, "dep-1") is not None


@pytest.mark.asyncio
async def test_concurrent_execute_dispatches_once(snapshots):
    import asyncio

    client, _dbmod, pve = snapshots
    item = await plan(client)
    results = await asyncio.gather(execute(client, item), execute(client, item))
    assert sorted(row.status_code for row in results) == [202, 409]
    assert len(pve.writes) == 2


@pytest.mark.asyncio
async def test_retention_refuses_corrupt_policy_and_native_errors_are_redacted(
    snapshots,
):
    from app.core.config import Settings
    from pathlib import Path

    client, _dbmod, pve = snapshots
    item = await complete(client, pve)
    Path(Settings().workspace_root, "retention.json").write_text(
        '{"keep_count": -1, "keep_days": 0}'
    )
    response = await client.post("/v1/deployments/dep-1/snapshot-sets/retention/plan")
    assert response.status_code == 409
    assert response.json()["code"] == "RETENTION_POLICY_UNAVAILABLE"
    pve.tasks.clear()
    item = await plan(client)
    pve.ambiguous_vmid = 5000
    response = await execute(client, item)
    assert (
        "private upstream" not in response.text
        and "test-proxmox-secret" not in response.text
    )


@pytest.mark.asyncio
async def test_new_endpoints_require_backend_bearer_before_reads(tmp_path, monkeypatch):
    monkeypatch.setenv("RANGE42_AUTH_MODE", "required")
    monkeypatch.setenv("RANGE42_API_TOKEN", "test-token-only-" * 3)
    app, dbmod = await _boot(tmp_path, monkeypatch)
    from app import main
    from app.core import config

    monkeypatch.setattr(main, "settings", config.settings)
    app = main.create_app()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as client:
            assert (
                await client.post("/v1/deployments/dep-1/snapshot-sets/plan", json={})
            ).status_code == 401
            assert (
                await client.get("/v1/deployments/dep-1/snapshot-sets")
            ).status_code == 401
            assert (
                await client.get(
                    "/v1/deployments/dep-1/snapshot-sets",
                    headers={"Authorization": "Bearer " + "test-token-only-" * 3},
                )
            ).status_code == 404
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path,body",
    [
        ("POST", "/v1/proxmox/hosts/h/vms/5000/snapshots", {"snapname": "manual"}),
        ("DELETE", "/v1/proxmox/hosts/h/vms/5000/snapshots/manual", None),
        ("POST", "/v1/proxmox/hosts/h/vms/5000/snapshots/manual/rollback", None),
        ("POST", "/v1/proxmox/hosts/h/vms/5000/status/stop", None),
        ("DELETE", "/v1/proxmox/hosts/h/vms/5000", None),
    ],
)
async def test_per_vm_mutation_cannot_bypass_active_set(snapshots, method, path, body):
    client, _dbmod, pve = snapshots
    item = await plan(client)
    assert (await execute(client, item)).status_code == 202
    response = await client.request(method, path, json=body)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "SNAPSHOT_MEMBER_BUSY"
    assert len(pve.writes) == 2
    assert (
        await client.get("/v1/proxmox/hosts/h/vms/5000/snapshots")
    ).status_code == 200


@pytest.mark.asyncio
async def test_unknown_dispatch_publicly_requires_operator_recovery(snapshots):
    client, _dbmod, pve = snapshots
    item = await plan(client)
    pve.ambiguous_vmid = 5000
    response = await execute(client, item)
    assert response.json()["operation"]["recovery"] == "operator_required"
    assert (await reconcile(client, item)).json()["operation"][
        "recovery"
    ] == "operator_required"


@pytest.mark.asyncio
async def test_marker_removal_and_host_alias_do_not_bypass_active_set_config_guard(
    snapshots,
):
    client, dbmod, pve = snapshots
    item = await plan(client)
    assert (await execute(client, item)).status_code == 202
    pve.configs[5000]["description"] = ""
    from app.core.models import ProxmoxHost

    async with dbmod.get_session_factory()() as session:
        session.add(
            ProxmoxHost(
                id="alias",
                name="alias",
                api_url="http://127.0.0.1:1",
                node_name="pve01",
                token_ref="test-only",
            )
        )
        await session.commit()
    response = await client.put(
        "/v1/proxmox/hosts/alias/vms/5000/config",
        json={"digest": "a" * 64, "changes": {"name": "changed"}},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "SNAPSHOT_MEMBER_BUSY"
    assert len(pve.writes) == 2
