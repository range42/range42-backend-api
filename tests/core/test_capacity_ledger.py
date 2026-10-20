"""Actual SQLite serialization retains one canonical nonexpiring hold per run."""

import asyncio
from importlib.util import find_spec

import pytest
from sqlalchemy import select, func

from tests.core.test_capacity_accounting import observation
from tests.routes.test_deployments_preflight import _boot, _seed


def services():
    assert find_spec("app.core.capacity_ledger") is not None, (
        "Missing durable serialized physical-capacity ledger"
    )
    from app.core import capacity_ledger

    return capacity_ledger


@pytest.fixture
async def ledger(tmp_path, monkeypatch):
    mod = services()
    app, db = await _boot(tmp_path, monkeypatch)
    await _seed(db, tmp_path)
    from app.core.models import Deployment, ProxmoxHost
    from app.core.physical_nodes import (
        review_observation,
        save_observation,
    )
    from app.core.workshop_runs import WorkshopSpec, compile_plan
    from app.core.deployment_allocations import bind
    from sqlalchemy import text

    factory = db.get_session_factory()
    async with factory() as session:
        host = await session.get(ProxmoxHost, "h")
        host.node_name = "pve-test"
        host.api_url = "https://pve.invalid:8006"
        session.add(
            ProxmoxHost(
                id="alias",
                name="alias",
                node_name="pve-test",
                api_url="https://alias.invalid:8006",
                token_ref="different-synthetic-credential",
            )
        )
        first = await session.get(Deployment, "dep-1")
        first.id = "a" * 16
        session.add(
            Deployment(
                id="b" * 16,
                codename="second",
                scenario_label="workshop",
                project_id="p",
                target_host_id="alias",
                project_sha="1" * 40,
                workspace_path=str(tmp_path / "second"),
            )
        )
        await session.commit()
    for index, (deployment_id, host_id) in enumerate(
        [("a" * 16, "h"), ("b" * 16, "alias")]
    ):
        async with factory() as session:
            host = await session.get(ProxmoxHost, host_id)
            from app.core.physical_nodes import target_revision

            observed = await review_observation(
                session,
                host_id,
                observation().node,
                agent_revision="f" * 64,
                target_revision_at_observation=target_revision(host),
            )
            await save_observation(
                session, observed, review_revision=observed.revision, actor="director"
            )
        async with factory() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            dep = await session.get(Deployment, deployment_id)
            host = await session.get(ProxmoxHost, host_id)
            spec = WorkshopSpec(
                namespace="workshop" + str(index),
                seats=1,
                expires_at="2099-10-20T00:00:00+00:00",
                vmid_start=46000 + index * 10,
                network_pool="10.99." + str(index) + ".0/28",
                bridge_start=600 + index,
                proxmox_template=9240,
                deployer_template=9241,
                storage="ceph-range",
            )
            plan = compile_plan(spec, run_id=deployment_id)
            dep.project_sha = "1" * 40
            dep.native = {"workshop": spec.model_dump(), "workshop_plan": plan}
            await bind(session, dep, host, plan["assignments"])
            await session.commit()
    yield mod, factory
    await db.dispose_engine()


@pytest.mark.asyncio
async def test_two_aliases_and_concurrent_reservations_cannot_both_spend_the_same_ram(
    ledger,
):
    mod, factory = ledger
    first = await mod.review(factory, "a" * 16, observation(), agent_revision="f" * 64)
    second = await mod.review(factory, "b" * 16, observation(), agent_revision="f" * 64)
    values = await asyncio.gather(
        mod.reserve(factory, first, actor="director"),
        mod.reserve(factory, second, actor="director"),
        return_exceptions=True,
    )
    from app.core.errors import Range42Error
    from app.core.capacity_models import CapacityHold

    assert sum(isinstance(value, CapacityHold) for value in values) == 1
    errors = [value for value in values if isinstance(value, Range42Error)]
    assert len(errors) == 1 and errors[0].status == 409
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(CapacityHold)) == 1
        row = await session.scalar(select(CapacityHold))
        assert row.status == "pending_root" and row.physical_node_id == "a" * 32


async def reserve_first(mod, factory):
    approved = await mod.review(factory, 'a' * 16, observation(), agent_revision='f' * 64)
    row = await mod.reserve(factory, approved, actor='director')
    fresh = await mod.review(factory, 'a' * 16, observation(), agent_revision='f' * 64)
    return row, fresh


@pytest.mark.asyncio
async def test_unused_absent_intent_retires_once_and_releases_only_its_commitment(ledger):
    mod, factory = ledger
    original, fresh = await reserve_first(mod, factory)
    assert hasattr(mod, 'retire_unused'), 'Missing verified retirement for an unused intent'
    retired = await mod.retire_unused(factory, fresh, actor='second-director', expected_revision=fresh.revision)
    assert retired.id == original.id and retired.status == 'retired'
    assert retired.retired_by == 'second-director' and retired.native_request_id is None
    assert mod.commitment(retired).phase == 'retired'
    repeated = await mod.retire_unused(factory, fresh, actor='another-director', expected_revision=fresh.revision)
    assert repeated.revision == retired.revision and repeated.retired_by == 'second-director'
    second = await mod.review(factory, 'b' * 16, observation(), agent_revision='f' * 64)
    assert second.assessment['admissible'] is True
    from app.core.capacity_models import CapacityHold
    from app.core.allocation_models import DeploymentAllocation
    async with factory() as session:
        claim = await session.scalar(select(DeploymentAllocation).where(DeploymentAllocation.deployment_id == 'a' * 16))
        assert claim is not None
        await mod.assert_allocation_releasable(session, claim)
        assert await session.scalar(select(func.count()).select_from(CapacityHold)) == 1


@pytest.mark.asyncio
async def test_journaled_native_request_cannot_be_mistaken_for_unused_after_a_lost_reply(ledger):
    mod, factory = ledger
    row, fresh = await reserve_first(mod, factory)
    assert hasattr(mod, 'journal_native_request'), 'Missing durable pre-RPC native request journal'
    sent = await mod.journal_native_request(factory, fresh, actor='director', expected_revision=row.approval_revision)
    assert sent.native_request_id and sent.native_request_actor == 'director'
    again = await mod.journal_native_request(factory, fresh, actor='other-director', expected_revision=row.approval_revision)
    assert again.native_request_id == sent.native_request_id and again.native_request_actor == 'director'
    current = await mod.review(factory, 'a' * 16, observation(), agent_revision='f' * 64)
    from app.core.errors import Range42Error
    with pytest.raises(Range42Error) as refused:
        await mod.retire_unused(factory, current, actor='director', expected_revision=current.revision)
    assert refused.value.code == 'CAPACITY_NATIVE_RECONCILIATION_REQUIRED'


@pytest.mark.asyncio
@pytest.mark.parametrize('uncertain', ['guest', 'attempt', 'unknown', 'workspace', 'stale'])
async def test_unused_retirement_refuses_materialized_busy_or_stale_state(ledger, uncertain):
    mod, factory = ledger
    row, fresh = await reserve_first(mod, factory)
    from app.core.models import Deployment, Attempt, WorkspaceLock
    from app.core.capacity_models import CapacityHold
    from app.core.errors import Range42Error
    if uncertain == 'guest':
        observed = observation().model_copy(deep=True)
        observed.resources.guests[0].vm_id = 46000
        fresh = await mod.review(factory, 'a' * 16, observed, agent_revision='f' * 64)
    elif uncertain != 'stale':
        async with factory() as session:
            if uncertain == 'attempt':
                session.add(Attempt(id='pending-native', deployment_id='a' * 16, scope='full', state='unknown'))
            elif uncertain == 'unknown':
                (await session.get(Deployment, 'a' * 16)).state = 'unknown'
            else:
                from datetime import datetime, timezone
                session.add(WorkspaceLock(deployment_id='a' * 16, owner='another-worker', heartbeat_at=datetime.now(timezone.utc)))
            await session.commit()
    assert hasattr(mod, 'retire_unused'), 'Missing verified unused-intent retirement'
    with pytest.raises(Range42Error):
        await mod.retire_unused(factory, fresh, actor='director', expected_revision='0' * 64 if uncertain == 'stale' else fresh.revision)
    async with factory() as session:
        assert (await session.get(CapacityHold, row.id)).status == 'pending_root'


@pytest.mark.asyncio
async def test_native_journal_and_unused_retirement_cannot_both_win(ledger):
    mod, factory = ledger
    row, fresh = await reserve_first(mod, factory)
    assert hasattr(mod, 'retire_unused') and hasattr(mod, 'journal_native_request'), 'Missing atomic capacity lifecycle'
    results = await asyncio.gather(
        mod.retire_unused(factory, fresh, actor='director', expected_revision=fresh.revision),
        mod.journal_native_request(factory, fresh, actor='director', expected_revision=row.approval_revision),
        return_exceptions=True)
    from app.core.capacity_models import CapacityHold
    from app.core.errors import Range42Error
    assert sum(isinstance(result, CapacityHold) for result in results) == 1
    assert sum(isinstance(result, Range42Error) for result in results) == 1
    async with factory() as session:
        held = await session.get(CapacityHold, row.id)
        assert (held.status == 'retired') != (held.native_request_id is not None)


@pytest.mark.asyncio
async def test_unknown_attempt_outcome_blocks_a_new_reservation_even_when_deployment_is_pending(ledger):
    mod, factory = ledger
    from app.core.models import Attempt
    from app.core.errors import Range42Error
    async with factory() as session:
        session.add(Attempt(id='uncertain-prior', deployment_id='a' * 16, scope='full', state='unknown'))
        await session.commit()
    fresh = await mod.review(factory, 'a' * 16, observation(), agent_revision='f' * 64)
    with pytest.raises(Range42Error) as refused:
        await mod.reserve(factory, fresh, actor='director')
    assert refused.value.code == 'CAPACITY_WORK_IN_PROGRESS'


@pytest.mark.asyncio
async def test_expiry_prevents_a_first_native_request_but_allows_unused_cleanup(ledger, monkeypatch):
    from datetime import datetime, timezone
    from app.core.errors import Range42Error
    from app.core.workshop_runs import validate_activation
    mod, factory = ledger
    row, fresh = await reserve_first(mod, factory)
    monkeypatch.setattr(mod, 'validate_activation', lambda plan: validate_activation(plan, now=datetime(2100, 1, 1, tzinfo=timezone.utc)))
    with pytest.raises(Range42Error) as refused:
        await mod.journal_native_request(factory, fresh, actor='director', expected_revision=row.approval_revision)
    assert refused.value.code == 'WORKSHOP_EXPIRED'
    retired = await mod.retire_unused(factory, fresh, actor='director', expected_revision=fresh.revision)
    assert retired.status == 'retired'


@pytest.mark.asyncio
async def test_lost_reservation_reply_reuses_original_hold_and_terminal_attempt_does_not_release_it(
    ledger,
):
    mod, factory = ledger
    reviewed = await mod.review(
        factory, "a" * 16, observation(), agent_revision="f" * 64
    )
    first = await mod.reserve(factory, reviewed, actor="director")
    from app.core.models import Deployment

    async with factory() as session:
        dep = await session.get(Deployment, "a" * 16)
        dep.state = "failed"
        await session.commit()
    repeated = await mod.reserve(factory, reviewed, actor="other-director")
    assert repeated.id == first.id and repeated.approved_by == "director"
    second = await mod.review(factory, "b" * 16, observation(), agent_revision="f" * 64)
    assert second.assessment["admissible"] is False
    from app.core.errors import Range42Error

    with pytest.raises(Range42Error):
        await mod.reserve(factory, second, actor="director")


@pytest.mark.asyncio
async def test_changed_server_plan_or_allocation_refuses_old_review_without_creating_a_hold(
    ledger,
):
    mod, factory = ledger
    reviewed = await mod.review(
        factory, "a" * 16, observation(), agent_revision="f" * 64
    )
    from app.core.models import Deployment

    async with factory() as session:
        dep = await session.get(Deployment, "a" * 16)
        native = {**dep.native}
        native["workshop"] = {**native["workshop"], "namespace": "changed"}
        dep.native = native
        await session.commit()
    from app.core.errors import Range42Error

    with pytest.raises(Range42Error):
        await mod.reserve(factory, reviewed, actor="director")
    from app.core.capacity_models import CapacityHold

    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(CapacityHold)) == 0


@pytest.mark.asyncio
async def test_corrupt_retained_hold_never_becomes_free_capacity(ledger):
    mod, factory = ledger
    reviewed = await mod.review(
        factory, "a" * 16, observation(), agent_revision="f" * 64
    )
    row = await mod.reserve(factory, reviewed, actor="director")
    from app.core.capacity_models import CapacityHold

    async with factory() as session:
        saved = await session.get(CapacityHold, row.id)
        saved.record_json = "{}"
        await session.commit()
    from app.core.errors import Range42Error

    with pytest.raises(Range42Error):
        await mod.review(factory, "b" * 16, observation(), agent_revision="f" * 64)


@pytest.mark.asyncio
async def test_capacity_cannot_be_reserved_while_an_unreserved_attempt_is_running(
    ledger,
):
    mod, factory = ledger
    reviewed = await mod.review(
        factory, "a" * 16, observation(), agent_revision="f" * 64
    )
    from app.core.models import Attempt

    async with factory() as session:
        session.add(
            Attempt(id="running", deployment_id="a" * 16, scope="all", state="running")
        )
        await session.commit()
    from app.core.errors import Range42Error

    with pytest.raises(Range42Error) as error:
        await mod.reserve(factory, reviewed, actor="director")
    assert error.value.code == "CAPACITY_WORK_IN_PROGRESS"


@pytest.mark.asyncio
async def test_pending_root_intent_does_not_grant_execution_or_allow_allocation_release(
    ledger,
):
    mod, factory = ledger
    reviewed = await mod.review(
        factory, "a" * 16, observation(), agent_revision="f" * 64
    )
    await mod.reserve(factory, reviewed, actor="director")
    assert hasattr(mod, "assert_execution_accepted"), (
        "Missing capacity intent execution gate"
    )
    assert hasattr(mod, "assert_allocation_releasable"), (
        "Missing canonical release capacity gate"
    )
    from app.core.errors import Range42Error
    from app.core.allocation_models import DeploymentAllocation

    async with factory() as session:
        with pytest.raises(Range42Error) as error:
            await mod.assert_execution_accepted(session, "a" * 16)
        assert error.value.code == "CAPACITY_ROOT_ACCEPTANCE_REQUIRED"
        claim = await session.scalar(
            select(DeploymentAllocation).where(
                DeploymentAllocation.deployment_id == "a" * 16
            )
        )
        with pytest.raises(Range42Error) as error:
            await mod.assert_allocation_releasable(session, claim)
        assert error.value.code == "CAPACITY_RETIREMENT_REQUIRED"


@pytest.mark.asyncio
async def test_actual_allocation_release_keeps_pending_capacity_and_vm_assignments(
    ledger, monkeypatch
):
    mod, factory = ledger
    reviewed = await mod.review(
        factory, "a" * 16, observation(), agent_revision="f" * 64
    )
    await mod.reserve(factory, reviewed, actor="director")
    from app.routes.v1.deployments import allocations as routes

    probes = []

    async def absent(*args):
        probes.append(True)
        return True

    monkeypatch.setattr(routes, "vmid_is_free", absent)
    from app.main import create_app
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        response = await client.delete("/v1/deployments/" + "a" * 16 + "/allocations")
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "CAPACITY_RETIREMENT_REQUIRED"
    assert probes == []
    from app.core.allocation_models import DeploymentAllocation

    async with factory() as session:
        assert (
            await session.scalar(select(func.count()).select_from(DeploymentAllocation))
            == 2
        )


@pytest.mark.asyncio
async def test_an_active_database_phase_alone_is_not_a_native_execution_attestation(
    ledger,
):
    mod, factory = ledger
    reviewed = await mod.review(
        factory, "a" * 16, observation(), agent_revision="f" * 64
    )
    saved = await mod.reserve(factory, reviewed, actor="director")
    from app.core.capacity_models import CapacityHold

    async with factory() as session:
        row = await session.get(CapacityHold, saved.id)
        row.status = "active"
        row.record_digest = mod.record_digest(row)
        await session.commit()
    from app.core.errors import Range42Error

    async with factory() as session:
        with pytest.raises(Range42Error) as error:
            await mod.assert_execution_accepted(session, "a" * 16)
        assert error.value.code == "CAPACITY_ROOT_ACCEPTANCE_REQUIRED"


@pytest.mark.asyncio
async def test_opaque_review_revision_is_required_and_original_revision_survives_a_lost_reply(
    ledger,
):
    mod, factory = ledger
    reviewed = await mod.review(
        factory, "a" * 16, observation(), agent_revision="f" * 64
    )
    assert hasattr(reviewed, "revision"), (
        "Missing opaque server-derived capacity review revision"
    )
    assert len(reviewed.revision) == 64
    from app.core.errors import Range42Error

    with pytest.raises(Range42Error):
        await mod.reserve(
            factory, reviewed, actor="director", expected_revision="0" * 64
        )
    first = await mod.reserve(
        factory, reviewed, actor="director", expected_revision=reviewed.revision
    )
    fresh = await mod.review(factory, "a" * 16, observation(), agent_revision="f" * 64)
    assert fresh.revision != reviewed.revision
    again = await mod.reserve(
        factory, fresh, actor="director", expected_revision=reviewed.revision
    )
    assert first.id == again.id and again.approval_revision == reviewed.revision
    with pytest.raises(Range42Error):
        await mod.reserve(factory, fresh, actor="director", expected_revision="0" * 64)


@pytest.mark.asyncio
async def test_changing_free_space_does_not_hide_config_changes_or_make_review_unusable(
    ledger,
):
    mod, factory = ledger
    first = await mod.review(factory, "a" * 16, observation(), agent_revision="f" * 64)
    assert hasattr(first, "revision"), "Missing stable capacity review binding"
    changed = observation().model_copy(deep=True)
    changed.resources.storage[0].logical_available_bytes -= 1024
    changed.resources.storage[0].cluster_raw_available_bytes -= 3072
    refreshed = await mod.review(factory, "a" * 16, changed, agent_revision="f" * 64)
    assert refreshed.revision == first.revision
    changed.resources.guests[0].configuration_revision = "9" * 64
    drifted = await mod.review(factory, "a" * 16, changed, agent_revision="f" * 64)
    assert drifted.revision != first.revision


@pytest.mark.asyncio
async def test_changed_native_source_path_invalidates_a_capacity_review(ledger):
    mod, factory = ledger
    reviewed = await mod.review(
        factory, "a" * 16, observation(), agent_revision="f" * 64
    )
    from app.core.models import Deployment

    async with factory() as session:
        dep = await session.get(Deployment, "a" * 16)
        dep.native = {**dep.native, "path": "training/different-executable"}
        await session.commit()
    from app.core.errors import Range42Error

    with pytest.raises(Range42Error):
        await mod.reserve(factory, reviewed, actor="director")


@pytest.mark.asyncio
async def test_attempt_creation_and_direct_worker_dispatch_require_native_capacity_acceptance(
    ledger, monkeypatch
):
    mod, factory = ledger
    reviewed = await mod.review(
        factory, "a" * 16, observation(), agent_revision="f" * 64
    )
    await mod.reserve(factory, reviewed, actor="director")
    from app.main import create_app
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/deployments/" + "a" * 16 + "/attempts", json={"scope": "full"}
        )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "CAPACITY_ROOT_ACCEPTANCE_REQUIRED"
    from app.core.models import Attempt

    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(Attempt)) == 0
        pending = Attempt(
            id="stale-worker", deployment_id="a" * 16, scope="full", state="pending"
        )
        session.add(pending)
        await session.commit()
    from app.core import deploy_trigger

    calls = []

    async def spawn(*args, **kwargs):
        calls.append(True)

    monkeypatch.setattr(deploy_trigger, "_start_attempt", spawn)
    from app.core.errors import Range42Error

    async with factory() as session:
        row = await session.get(Attempt, "stale-worker")
        with pytest.raises(Range42Error):
            await deploy_trigger.start_attempt(session, attempt=row)
    assert calls == []


@pytest.mark.asyncio
async def test_raw_vm_alias_writes_and_unscoped_legacy_writers_cannot_bypass_a_pending_hold(
    ledger, monkeypatch
):
    mod, factory = ledger
    reviewed = await mod.review(
        factory, "a" * 16, observation(), agent_revision="f" * 64
    )
    await mod.reserve(factory, reviewed, actor="director")
    from httpx import ASGITransport, AsyncClient, Response
    from app.routes.v1.proxmox import vms

    calls = []

    class NativeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, *args, **kwargs):
            calls.append(True)
            return Response(200, json={"data": "UPID:synthetic"})

    monkeypatch.setattr(vms.httpx, "AsyncClient", NativeClient)
    from app.main import create_app

    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        response = await client.post("/v1/proxmox/hosts/alias/vms/46000/status/start")
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "CAPACITY_ROOT_ACCEPTANCE_REQUIRED"
        response = await client.post("/v0/do-not-dispatch", json={})
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "CAPACITY_UNSCOPED_WRITE_PAUSED"
    assert calls == []


@pytest.mark.asyncio
async def test_ordinary_native_defaults_are_not_treated_as_a_managed_workshop(ledger):
    mod, factory = ledger
    from app.core.models import Deployment

    async with factory() as session:
        session.add(
            Deployment(
                id="ordinary",
                codename="ordinary",
                scenario_label="example",
                project_id="p",
                target_host_id="h",
                workspace_path="/not-used",
                native={
                    "path": "training/example",
                    "workshop": None,
                    "workshop_plan": None,
                },
            )
        )
        await session.commit()
    async with factory() as session:
        await mod.assert_dispatch_accepted(session, "ordinary")


@pytest.mark.asyncio
async def test_unplanned_provisioning_cannot_consume_resources_already_reserved_for_a_run(
    ledger,
):
    mod, factory = ledger
    reviewed = await mod.review(
        factory, "a" * 16, observation(), agent_revision="f" * 64
    )
    await mod.reserve(factory, reviewed, actor="director")
    from app.core.models import Deployment

    async with factory() as session:
        session.add(
            Deployment(
                id="ordinary",
                codename="ordinary",
                scenario_label="example",
                project_id="p",
                target_host_id="h",
                workspace_path="/not-used",
                native={"path": "training/example", "workshop": None},
            )
        )
        await session.commit()
    from app.core.errors import Range42Error

    async with factory() as session:
        with pytest.raises(Range42Error) as error:
            await mod.assert_dispatch_accepted(session, "ordinary")
        assert error.value.code == "CAPACITY_PLAN_REQUIRED"
        with pytest.raises(Range42Error) as error:
            await mod.assert_vm_capacity_accepted(session, 45000)
        assert error.value.code == "CAPACITY_PLAN_REQUIRED"
