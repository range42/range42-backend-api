"""Real API/DB guards with simulated PVE; no guest is created or changed."""

import copy
from urllib.parse import parse_qs

import httpx
import pytest

from tests.routes.test_vm_config_edit import api, SECRET

BASE = "/v1/proxmox/hosts/selected/vms/60001/hardware"


def seed(pve):
    pve.config.update(
        net0="virtio=52:54:00:00:00:01,bridge=vmbr0,tag=5,queues=4,mtu=1400",
        net10="e1000=52:54:00:00:00:02,bridge=vmbr0",
        scsi0="local-lvm:vm-60001-disk-0,size=16G,discard=on",
        ide2="local-lvm:vm-60001-cloudinit,media=cdrom",
        unused0="local-lvm:vm-60001-disk-9",
    )
    pve.current = copy.deepcopy(pve.config)
    original = pve.respond
    pve.pool_free = 100 * 1024**3
    pve.bridge_active = True
    pve.resize_count = 0
    pve.resize_upid = pve.upid.replace(":qmconfig:", ":resize:")

    async def respond(request):
        if request.url.path.endswith("/network"):
            pve.calls.append(request)
            assert request.url.params["type"] == "any_bridge"
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"iface": "vmbr0", "type": "bridge", "active": 1},
                        {
                            "iface": "vmbr1",
                            "type": "bridge",
                            "active": int(pve.bridge_active),
                        },
                    ]
                },
            )
        if "/storage/" in request.url.path:
            pve.calls.append(request)
            assert request.url.path.endswith("/storage/local-lvm/status")
            return httpx.Response(
                200,
                json={
                    "data": {
                        "active": 1,
                        "enabled": 1,
                        "content": "images",
                        "avail": pve.pool_free,
                    }
                },
            )
        if request.url.path.endswith("/resize"):
            pve.calls.append(request)
            pve.resize_count += 1
            data = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            assert request.method == "PUT" and data["digest"] == pve.config["digest"]
            assert data["disk"] == "scsi0" and data["size"] == "24G"
            pve.config["scsi0"] = pve.config["scsi0"].replace("size=16G", "size=24G")
            pve.current["scsi0"] = pve.config["scsi0"]
            pve.config["digest"] = pve.current["digest"] = "b" * 40
            return httpx.Response(200, json={"data": pve.resize_upid})
        return await original(request)

    pve.respond = respond


async def review(client):
    response = await client.get(BASE + "/review")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_hardware_review_preserves_indexes_and_filters_raw_configuration(
    tmp_path, monkeypatch
):
    async with api(tmp_path, monkeypatch) as (client, pve, _):
        seed(pve)
        value = await review(client)
        assert [nic["id"] for nic in value["configured"]["nics"]] == ["net0", "net10"]
        assert value["configured"]["nics"][0]["mac"] == "52:54:00:00:00:01"
        assert [disk["id"] for disk in value["configured"]["disks"]] == ["scsi0"]
        assert value["configured"]["disks"][0]["size_bytes"] == 16 * 1024**3
        assert len(value["configured"]["disks"][0]["volume_fingerprint"]) == 64
        assert value["pending"] == [] and not pve.writes
        assert SECRET not in str(value) and "cipassword" not in str(value)
        assert "vm-60001-disk-0" not in str(value)


@pytest.mark.asyncio
async def test_nic_patch_preserves_original_mac_model_options_and_other_nics(
    tmp_path, monkeypatch
):
    async with api(tmp_path, monkeypatch) as (client, pve, _):
        seed(pve)
        before = await review(client)
        response = await client.put(
            BASE + "/nics/net0",
            json={
                "digest": before["digest"],
                "changes": {"bridge": "vmbr1", "tag": None, "firewall": True},
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "accepted"
        assert response.json()["upid"] == pve.upid
        assert len(pve.writes) == 1 and pve.writes[0].method == "POST"
        assert (
            pve.config["net0"]
            == "virtio=52:54:00:00:00:01,bridge=vmbr1,queues=4,mtu=1400,firewall=1"
        )
        assert pve.config["net10"] == "e1000=52:54:00:00:00:02,bridge=vmbr0"
        after = await review(client)
        assert after["pending"] == ["net0"]  # configured, not claimed hotplugged
        assert after["current"]["nics"][0]["bridge"] == "vmbr0"


@pytest.mark.asyncio
async def test_disk_growth_is_absolute_once_and_preserves_volume_identity(
    tmp_path, monkeypatch
):
    async with api(tmp_path, monkeypatch) as (client, pve, _):
        seed(pve)
        before = await review(client)
        response = await client.put(
            BASE + "/disks/scsi0/grow", json={"digest": before["digest"], "size_gb": 24}
        )
        assert response.status_code == 200, response.text
        assert (
            response.json()["status"] == "accepted"
            and response.json()["upid"] == pve.resize_upid
        )
        from tests.routes.test_vm_config_edit import config_task_url

        polled = await client.get(
            config_task_url(pve.resize_upid, before["target_digest"])
        )
        assert polled.status_code == 200 and polled.json()["status"] == "stopped"
        after = await review(client)
        assert after["configured"]["disks"][0]["size_bytes"] == 24 * 1024**3
        assert (
            after["configured"]["disks"][0]["volume_fingerprint"]
            == before["configured"]["disks"][0]["volume_fingerprint"]
        )
        assert pve.resize_count == 1
        stale = await client.put(
            BASE + "/disks/scsi0/grow", json={"digest": before["digest"], "size_gb": 24}
        )
        assert stale.status_code == 409 and pve.resize_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [
        "stale",
        "pending",
        "managed",
        "template",
        "locked",
        "missing",
        "ambiguous",
        "missing-mac",
        "bridge",
    ],
)
async def test_nic_refusal_never_sends_a_write(tmp_path, monkeypatch, kind):
    async with api(tmp_path, monkeypatch) as (client, pve, _):
        seed(pve)
        before = await review(client)
        if kind == "stale":
            pve.config["digest"] = pve.current["digest"] = "c" * 40
        elif kind == "pending":
            pve.current["net0"] = "virtio=52:54:00:00:00:01,bridge=vmbr2"
        elif kind == "managed":
            pve.config["description"] = "range42-deployment:owned"
        elif kind == "template":
            pve.config["template"] = 1
        elif kind == "locked":
            pve.config["lock"] = "backup"
        elif kind == "missing":
            del pve.config["net0"]
            del pve.current["net0"]
        elif kind == "ambiguous":
            pve.config["net0"] += ",bridge=vmbr2"
            pve.current["net0"] = pve.config["net0"]
        elif kind == "missing-mac":
            pve.config["net0"] = pve.current["net0"] = "virtio,bridge=vmbr0"
        elif kind == "bridge":
            pve.bridge_active = False
        response = await client.put(
            BASE + "/nics/net0",
            json={"digest": before["digest"], "changes": {"bridge": "vmbr1"}},
        )
        assert response.status_code == 409, response.text
        assert not pve.writes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    ["shrink", "same", "missing", "cdrom", "pending", "unknown-size", "no-space"],
)
async def test_disk_refusal_never_sends_a_write(tmp_path, monkeypatch, kind):
    async with api(tmp_path, monkeypatch) as (client, pve, _):
        seed(pve)
        before = await review(client)
        disk, size = "scsi0", 24
        if kind == "shrink":
            size = 8
        elif kind == "same":
            size = 16
        elif kind == "missing":
            disk = "scsi1"
        elif kind == "cdrom":
            disk = "ide2"
        elif kind == "pending":
            pve.current["scsi0"] = "local-lvm:vm-60001-disk-2,size=16G"
        elif kind == "unknown-size":
            pve.config["scsi0"] = pve.current["scsi0"] = "local-lvm:vm-60001-disk-0"
        elif kind == "no-space":
            pve.pool_free = 0
        response = await client.put(
            BASE + f"/disks/{disk}/grow",
            json={"digest": before["digest"], "size_gb": size},
        )
        assert response.status_code == 409, response.text
        assert not pve.writes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"mac": "other"},
        {"model": "e1000"},
        {"bridge": "../bad"},
        {"tag": 4095},
        {"firewall": 1},
    ],
)
async def test_unreviewed_nic_fields_and_invalid_types_are_rejected(
    tmp_path, monkeypatch, changes
):
    async with api(tmp_path, monkeypatch) as (client, pve, _):
        response = await client.put(
            BASE + "/nics/net0", json={"digest": "a" * 64, "changes": changes}
        )
        assert response.status_code == 422
        assert not pve.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "suffix,payload",
    [
        ("nics/net0", {"changes": {"firewall": True}}),
        ("disks/scsi0/grow", {"size_gb": 24}),
    ],
)
async def test_snapshot_member_protection_runs_before_hardware_reads_or_dispatch(
    tmp_path, monkeypatch, suffix, payload
):
    from app.core.errors import Range42Error
    from app.routes.v1.proxmox import vm_hardware

    async def held(session, vmid):
        assert vmid == 60001
        raise Range42Error(
            code="SNAPSHOT_MEMBER_BUSY", message="Snapshot member is busy", status=409
        )

    monkeypatch.setattr(vm_hardware, "_assert_snapshot_member_free", held)
    async with api(tmp_path, monkeypatch) as (client, pve, _):
        response = await client.put(
            BASE + "/" + suffix, json={"digest": "a" * 64, **payload}
        )
        assert (
            response.status_code == 409
            and response.json()["code"] == "SNAPSHOT_MEMBER_BUSY"
        )
        assert not pve.calls


@pytest.mark.asyncio
async def test_claim_without_marker_blocks_hardware_edit_before_pve(
    tmp_path, monkeypatch
):
    from datetime import datetime, timezone
    from app.core.allocation_models import DeploymentAllocation
    from app.core.models import Source, Project, Deployment

    async with api(tmp_path, monkeypatch) as (client, pve, factory):
        async with factory() as session:
            session.add(
                Source(
                    id="source",
                    provider="github",
                    base_url="https://github.com",
                    auth_kind="none",
                )
            )
            await session.flush()
            session.add(
                Project(
                    id="project",
                    name="project",
                    source_id="source",
                    branch_strategy="fixed",
                )
            )
            await session.flush()
            session.add(
                Deployment(
                    id="deployment",
                    codename="owned",
                    scenario_label="test",
                    project_id="project",
                    target_host_id="selected",
                    workspace_path=str(tmp_path),
                )
            )
            await session.flush()
            session.add(
                DeploymentAllocation(
                    id="claim",
                    deployment_id="deployment",
                    project_sha="e" * 40,
                    host_id="selected",
                    api_url="https://selected.test:8006",
                    node_name="pve-b",
                    assignments=[{"vm_id": 60001, "nics": []}],
                    created_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
        response = await client.put(
            BASE + "/nics/net0",
            json={"digest": "a" * 64, "changes": {"bridge": "vmbr1"}},
        )
        assert (
            response.status_code == 409
            and response.json()["code"] == "VM_CONFIG_MANAGED"
        )
        assert not pve.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 500])
async def test_provider_refusal_or_ambiguous_failure_never_retries(
    tmp_path, monkeypatch, status
):
    async with api(tmp_path, monkeypatch) as (client, pve, _):
        seed(pve)
        before = await review(client)
        pve.write_status = status
        response = await client.put(
            BASE + "/nics/net0",
            json={"digest": before["digest"], "changes": {"firewall": True}},
        )
        assert len(pve.writes) == 1
        assert SECRET not in response.text
        if status == 403:
            assert response.status_code == 403
        else:
            assert (
                response.status_code == 200
                and response.json()["status"] == "unconfirmed"
            )
