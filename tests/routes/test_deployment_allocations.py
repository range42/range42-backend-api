"""Draft-to-deployment ownership uses pinned Git, real SQLite and read-only PVE."""
import json
import asyncio
from copy import deepcopy

import pytest
from sqlalchemy import text

from app.core.models import Project, Source
from tests.core.test_project_repository import make_repository
from tests.routes import test_allocation_reservations as allocation_tests
from tests.routes.test_allocation_reservations import HEADERS, OTHER_TOKEN, plan

env = allocation_tests.env


async def saved_request(env, tmp_path, monkeypatch):
    client, _, db, url = env
    lease = (await client.post(url, json=plan(), headers=HEADERS)).json()
    vm = lease["assignments"][0]
    nic = vm["nics"][0]
    manifest = {"version": 3, "scenario": "content", "vms": [{
        "vm_id": vm["vm_id"], "vm_name": "guest", "ip": nic["ip"], "bridge": nic["bridge"],
        "nics": [{key: nic[key] for key in ("index", "ip", "bridge", "prefix")}],
    }]}
    inventory = {"all": {"children": {"scenario_guests": {
        "hosts": {"guest": {"ansible_host": nic["ip"]}},
    }}}}
    repo = tmp_path / "repo"
    sha = make_repository(repo, {
        "scenarios/content/main.yml": "- hosts: guest\n  tasks: []\n",
        "scenarios/content/hosts.yml": json.dumps(inventory),
        "scenarios/content/manifest/scenario_vms.json": json.dumps(manifest),
    })
    from app.core import scenario
    from app.core.project import checkout_repository
    def checkout_fixture(*, repo_url, **kwargs):
        assert repo_url == "https://github.com/owner/project.git"
        return checkout_repository(repo_url=repo.as_uri(), **kwargs)
    monkeypatch.setattr(scenario, "checkout_repository", checkout_fixture)
    async with db.get_session_factory()() as session:
        session.add(Source(id="s", provider="github", base_url="https://github.com", auth_kind="none"))
        await session.flush()
        session.add(Project(id="p", name="project", source_id="s", branch_strategy="shared_repo_subdir",
                            repo_owner="owner", repo_name="project"))
        await session.commit()
    return {"codename": "CLAIM", "scenario_label": "content", "project_id": "p",
            "target_host_id": lease["host_id"], "team_count": 1, "project_sha": sha,
            "allocation_reservation_id": lease["reservation_id"]}, lease


@pytest.mark.asyncio
async def test_creation_consumes_exact_lease_and_claim_survives_restart_and_expiry(env, tmp_path, monkeypatch):
    client, _, db, url = env
    payload, lease = await saved_request(env, tmp_path, monkeypatch)
    response = await client.post("/v1/deployments/", json=payload, headers=HEADERS)
    assert response.status_code == 201, response.text
    deployment_id = response.json()["id"]
    await db.dispose_engine()
    claim = await client.get(f"/v1/deployments/{deployment_id}/allocations")
    assert claim.status_code == 200, claim.text
    assert claim.json()["project_sha"] == payload["project_sha"]
    assert claim.json()["assignments"][0]["vm_id"] == lease["assignments"][0]["vm_id"]
    assert "token" not in claim.text and "expires_at" not in claim.text
    async with db.get_session_factory()() as session:
        assert await session.scalar(text("SELECT count(*) FROM allocation_reservations")) == 0
    again = await client.post(url, json=plan("other-draft"), headers=HEADERS)
    assert again.status_code == 200, again.text
    assert again.json()["assignments"][0]["vm_id"] != lease["assignments"][0]["vm_id"]
    assert again.json()["assignments"][0]["nics"][0]["ip"] != lease["assignments"][0]["nics"][0]["ip"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["token", "expired", "mapping", "node", "api_url", "no_sha", "missing_header"])
async def test_invalid_handoff_leaves_lease_and_deployment_untouched(env, tmp_path, monkeypatch, change):
    client, _, db, _ = env
    payload, lease = await saved_request(env, tmp_path, monkeypatch)
    headers = HEADERS
    async with db.get_session_factory()() as session:
        if change == "expired":
            await session.execute(text("UPDATE allocation_reservations SET expires_at = '2000-01-01'"))
        if change == "mapping":
            assignments = deepcopy(lease["assignments"])
            assignments[0]["nics"][0]["ip"] = "10.42.8.6"
            await session.execute(text("UPDATE allocation_reservations SET assignments = :value"), {"value": json.dumps(assignments)})
        if change == "node":
            await session.execute(text("UPDATE proxmox_hosts SET node_name = 'another'"))
        if change == "api_url":
            await session.execute(text("UPDATE proxmox_hosts SET api_url = 'https://another:8006'"))
        await session.commit()
    if change == "token":
        headers = {"X-Range42-Reservation-Token": OTHER_TOKEN}
    if change == "missing_header":
        headers = {}
    if change == "no_sha":
        payload.pop("project_sha")
    response = await client.post("/v1/deployments/", json=payload, headers=headers)
    assert response.status_code in (403, 409, 422), response.text
    async with db.get_session_factory()() as session:
        assert await session.scalar(text("SELECT count(*) FROM deployments")) == 0
        assert await session.scalar(text("SELECT count(*) FROM allocation_reservations")) == 1


@pytest.mark.asyncio
async def test_release_requires_all_claimed_guests_absent_then_frees_assignments(env, tmp_path, monkeypatch):
    client, pve, db, url = env
    payload, lease = await saved_request(env, tmp_path, monkeypatch)
    created = await client.post("/v1/deployments/", json=payload, headers=HEADERS)
    assert created.status_code == 201, created.text
    endpoint = f"/v1/deployments/{created.json()['id']}/allocations"
    vmid = lease["assignments"][0]["vm_id"]
    pve.occupied.add(vmid)  # Deliberately absent from permission-filtered resources.
    refused = await client.delete(endpoint)
    assert refused.status_code == 409, refused.text
    assert (await client.get(endpoint)).status_code == 200
    pve.occupied.clear()
    pve.nextid_status = 403
    assert (await client.delete(endpoint)).status_code == 409
    assert (await client.get(endpoint)).status_code == 200
    pve.nextid_status = None
    released = await client.delete(endpoint)
    assert released.status_code == 204, released.text
    assert (await client.get(endpoint)).status_code == 404
    again = await client.post(url, json=plan("next"), headers=HEADERS)
    assert again.status_code == 200, again.text
    assert again.json()["assignments"][0]["vm_id"] == vmid


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["target", "active", "lock", "unknown"])
async def test_release_rejects_changed_target_or_unsettled_execution(env, tmp_path, monkeypatch, change):
    client, pve, db, _ = env
    payload, _ = await saved_request(env, tmp_path, monkeypatch)
    created = await client.post("/v1/deployments/", json=payload, headers=HEADERS)
    assert created.status_code == 201, created.text
    dep_id = created.json()["id"]
    from app.core.models import Attempt, Deployment, ProxmoxHost, WorkspaceLock
    async with db.get_session_factory()() as session:
        if change == "target":
            (await session.get(ProxmoxHost, payload["target_host_id"])).api_url = "https://elsewhere:8006"
        if change == "active":
            session.add(Attempt(id="running", deployment_id=dep_id, scope="full", state="deploying"))
        if change == "lock":
            session.add(WorkspaceLock(deployment_id=dep_id, owner="still-held"))
        if change == "unknown":
            (await session.get(Deployment, dep_id)).state = "unknown"
        await session.commit()
    assert (await client.delete(f"/v1/deployments/{dep_id}/allocations")).status_code == 409
    assert not pve.probes[1:], "Release must reject before network probing"


@pytest.mark.asyncio
async def test_failed_commit_restores_lease_and_does_not_leave_a_claim(env, tmp_path, monkeypatch):
    client, _, db, _ = env
    payload, _ = await saved_request(env, tmp_path, monkeypatch)
    from sqlalchemy.ext.asyncio import AsyncSession
    async def fail_commit(self):
        raise RuntimeError("simulated commit failure")
    monkeypatch.setattr(AsyncSession, "commit", fail_commit)
    response = await client.post("/v1/deployments/", json=payload, headers=HEADERS)
    assert response.status_code == 500, response.text
    async with db.get_session_factory()() as session:
        assert await session.scalar(text("SELECT count(*) FROM deployments")) == 0
        assert await session.scalar(text("SELECT count(*) FROM deployment_allocations")) == 0
        assert await session.scalar(text("SELECT count(*) FROM allocation_reservations")) == 1


@pytest.mark.asyncio
async def test_concurrent_handoffs_cannot_share_one_lease(env, tmp_path, monkeypatch):
    client, _, db, _ = env
    payload, _ = await saved_request(env, tmp_path, monkeypatch)
    responses = await asyncio.gather(*(
        client.post("/v1/deployments/", json={**payload, "codename": f"CLAIM{i}"}, headers=HEADERS)
        for i in range(2)))
    assert sorted(response.status_code for response in responses) == [201, 409], [response.text for response in responses]
    async with db.get_session_factory()() as session:
        assert await session.scalar(text("SELECT count(*) FROM deployments")) == 1
        assert await session.scalar(text("SELECT count(*) FROM deployment_allocations")) == 1
        assert await session.scalar(text("SELECT count(*) FROM allocation_reservations")) == 0


@pytest.mark.asyncio
async def test_attempt_created_during_absence_probe_prevents_release_without_locking_database(env, tmp_path, monkeypatch):
    client, pve, db, _ = env
    payload, _ = await saved_request(env, tmp_path, monkeypatch)
    created = await client.post("/v1/deployments/", json=payload, headers=HEADERS)
    assert created.status_code == 201, created.text
    dep_id = created.json()["id"]
    original = pve.respond
    async def respond(request):
        if request.url.path.endswith("/cluster/nextid"):
            async def concurrent_writer():
                from app.core.models import Attempt, Deployment
                async with db.get_session_factory()() as session:
                    session.add(Attempt(id="new-attempt", deployment_id=dep_id, scope="full", state="succeeded"))
                    (await session.get(Deployment, dep_id)).current_attempt_id = "new-attempt"
                    await session.commit()
            await asyncio.wait_for(concurrent_writer(), 1)
        return original(request)
    monkeypatch.setattr(pve, "respond", respond)
    endpoint = f"/v1/deployments/{dep_id}/allocations"
    response = await client.delete(endpoint)
    assert response.status_code == 409, response.text
    assert (await client.get(endpoint)).status_code == 200


@pytest.mark.asyncio
async def test_full_attempt_cannot_bypass_an_existing_draft_lease(env, tmp_path, monkeypatch):
    client, _, db, _ = env
    payload, _ = await saved_request(env, tmp_path, monkeypatch)
    payload.pop("allocation_reservation_id")
    created = await client.post("/v1/deployments/", json=payload)
    assert created.status_code == 201, created.text
    from app.core import deploy_trigger
    from app.core.errors import Range42Error
    from app.core.models import Attempt
    from tests.fixtures.fake_runner import FakeRunner
    async def no_checks(*args, **kwargs):
        return []
    monkeypatch.setattr(deploy_trigger, "check_scenario_networks", no_checks)
    monkeypatch.setattr(deploy_trigger, "check_scenario_resources", no_checks)
    runner = FakeRunner(script=[])
    async with db.get_session_factory()() as session:
        attempt = Attempt(id="blocked-attempt", deployment_id=created.json()["id"], scope="full", state="pending")
        session.add(attempt)
        await session.commit()
        with pytest.raises(Range42Error, match="another draft"):
            await deploy_trigger.start_attempt(session, attempt=attempt, runner=runner)
    assert not deploy_trigger._BACKGROUND_TASKS
    async with db.get_session_factory()() as session:
        assert await session.scalar(text("SELECT count(*) FROM deployment_allocations")) == 0
