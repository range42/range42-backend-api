"""Only an original verified native receipt may record physical acceptance."""

import copy

import pytest
from sqlalchemy import select

from tests.core.test_capacity_ledger import reserve_first


@pytest.fixture
async def ledger(tmp_path, monkeypatch):
    from tests.core import test_capacity_ledger as base
    from tests.routes import test_capacity_holds as routes

    original = base.observation

    def observed():
        value = original()
        value.node.supported_operations = [
            "inspect",
            "capacity",
            "capacity-admit",
            "capacity-status",
        ]
        return value

    monkeypatch.setattr(base, "observation", observed)
    monkeypatch.setattr(routes, "observation", observed)
    async for value in base.ledger.__wrapped__(tmp_path, monkeypatch):
        yield value


def service():
    from app.core import native_capacity

    return native_capacity


def test_native_capacity_handshake_exists():
    from importlib.util import find_spec

    assert find_spec("app.core.native_capacity") is not None, (
        "Missing original native capacity receipt handshake"
    )


def receipt(request, observed):
    from app.core.physical_nodes import digest

    node = observed.node.model_dump()
    node["supported_operations"] = [
        "inspect",
        "capacity",
        "capacity-admit",
        "capacity-status",
    ]
    return {
        "protocol": "range42-native-capacity-receipt-v1",
        "status": "accepted",
        "request_id": request["request_id"],
        "request_digest": digest(request),
        "node": node,
        "receipt_revision": 1,
        "execution_ready": False,
        "infrastructure_qualified": False,
    }


@pytest.mark.asyncio
async def test_native_receipt_records_once_but_does_not_open_unqualified_execution(
    ledger,
):
    mod, factory = ledger
    original, fresh = await reserve_first(mod, factory)
    pending = await mod.journal_native_request(
        factory, fresh, actor="director", expected_revision=original.approval_revision
    )
    native = service()
    request = native.request_for(pending, fresh.observed.node.installation_id)
    proof = receipt(request, fresh.observed)
    accepted = await native.record_acceptance(factory, fresh, request, proof)
    assert (
        accepted.native_receipt_json is not None and accepted.status == "pending_root"
    )
    assert native.root_accepted(accepted) is True
    again = await native.record_acceptance(factory, fresh, request, proof)
    assert again.revision == accepted.revision
    from app.core.errors import Range42Error

    async with factory() as session:
        with pytest.raises(Range42Error) as failure:
            await mod.assert_execution_accepted(session, original.deployment_id)
        assert failure.value.code == "CAPACITY_ROOT_ACCEPTANCE_REQUIRED"
        from app.core.capacity_models import CapacityHold

        assert len((await session.scalars(select(CapacityHold))).all()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        "request",
        "plan",
        "node",
        "policy",
        "installation",
        "digest",
        "ready",
        "zero",
        "revision",
        "extra",
        "absent",
    ],
)
async def test_native_receipt_cannot_be_rebound_to_other_plans_or_claim_execution(
    ledger, damage
):
    mod, factory = ledger
    original, fresh = await reserve_first(mod, factory)
    pending = await mod.journal_native_request(
        factory, fresh, actor="director", expected_revision=original.approval_revision
    )
    native = service()
    request = native.request_for(pending, fresh.observed.node.installation_id)
    proof = receipt(request, fresh.observed)
    if damage == "request":
        proof["request_id"] = "a" * 32
    elif damage == "plan":
        request = {**request, "plan_revision": "a" * 64}
    elif damage == "node":
        proof["node"]["physical_node_id"] = "a" * 31 + "b"
    elif damage == "policy":
        proof["node"]["configuration_revision"] = "a" * 64
    elif damage == "installation":
        proof["node"]["installation_id"] = "a" * 32
    elif damage == "digest":
        proof["request_digest"] = "a" * 64
    elif damage == "ready":
        proof["execution_ready"] = True
    elif damage == "zero":
        proof["execution_ready"] = 0
    elif damage == "revision":
        proof["receipt_revision"] = True
    elif damage == "extra":
        proof["command"] = "id"
    elif damage == "absent":
        proof["status"] = "absent"
        proof["receipt_revision"] = 0
    from app.core.errors import Range42Error

    with pytest.raises(Range42Error):
        await native.record_acceptance(factory, fresh, request, proof)
    from app.core.capacity_models import CapacityHold

    async with factory() as session:
        row = await session.get(CapacityHold, original.id)
        assert (
            row.native_request_id
            and row.native_receipt_json is None
            and row.status == "pending_root"
        )


@pytest.mark.asyncio
async def test_receipt_tampering_after_persistence_cannot_pass_registry_validation(
    ledger,
):
    mod, factory = ledger
    original, fresh = await reserve_first(mod, factory)
    pending = await mod.journal_native_request(
        factory, fresh, actor="director", expected_revision=original.approval_revision
    )
    native = service()
    request = native.request_for(pending, fresh.observed.node.installation_id)
    await native.record_acceptance(
        factory, fresh, request, receipt(request, fresh.observed)
    )
    from app.core.capacity_models import CapacityHold
    from app.core.errors import Range42Error

    async with factory() as session:
        row = await session.get(CapacityHold, original.id)
        row.native_receipt_json = "{}"
        await session.commit()
    async with factory() as session:
        with pytest.raises(Range42Error):
            await mod.retained(session)


@pytest.mark.asyncio
async def test_pre_rpc_request_is_fully_synchronous_before_a_native_dispatch_can_escape(
    ledger,
):
    from sqlalchemy import event
    from app.core import db

    mod, factory = ledger
    original, fresh = await reserve_first(mod, factory)
    observed = []
    engine = db.get_engine().sync_engine

    def inspect_sync(connection, cursor, statement, parameters, context, executemany):
        if (
            statement.startswith("UPDATE capacity_holds SET")
            and "native_request_id=" in statement
        ):
            cursor.execute("PRAGMA synchronous")
            observed.append(cursor.fetchone()[0])

    event.listen(engine, "before_cursor_execute", inspect_sync)
    try:
        await mod.journal_native_request(
            factory,
            fresh,
            actor="director",
            expected_revision=original.approval_revision,
        )
    finally:
        event.remove(engine, "before_cursor_execute", inspect_sync)
    assert observed == [2], (
        "Pre-RPC request must use SQLite FULL durability before the native side can commit"
    )


@pytest.mark.asyncio
async def test_fixed_native_transport_never_accepts_arbitrary_actions_or_commands(
    tmp_path, monkeypatch
):
    from app.core import preparation_transport as transport
    from tests.core.test_preparation_transport import Process, config

    config(tmp_path, monkeypatch)
    selected = transport.agent_configuration("first", "pve-test")
    payload = {"synthetic": "only"}
    process = Process(b'{"ok":true,"result":{"status":"synthetic"}}')

    async def spawn(*args, **kwargs):
        return process

    monkeypatch.setattr(transport.asyncio, "create_subprocess_exec", spawn)
    await transport.read_agent(
        selected, action="capacity-admit", request=payload, timeout=110
    )
    import json

    assert json.loads(process.input) == {"action": "capacity-admit", "request": payload}
    from app.core.errors import Range42Error

    for action in ["id", "capacity", "inspect"]:
        with pytest.raises(Range42Error):
            await transport.read_agent(
                selected, action=action, request=copy.deepcopy(payload)
            )


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['materialized', 'retired'])
async def test_native_status_retains_original_request_after_owned_lifecycle_changes(ledger, phase):
    from app.core.capacity_models import CapacityHold
    from app.core.capacity_accounting import MaterializationReceipt
    from app.core.errors import Range42Error

    mod, factory = ledger
    original, fresh = await reserve_first(mod, factory)
    pending = await mod.journal_native_request(
        factory, fresh, actor='director', expected_revision=original.approval_revision)
    native = service()
    request = native.request_for(pending, fresh.observed.node.installation_id)
    await native.record_acceptance(factory, fresh, request, receipt(request, fresh.observed))
    async with factory() as session:
        row = await session.get(CapacityHold, original.id)
        held = mod.commitment(row)
        held.guests[0].materialization = MaterializationReceipt(
            vmgenid='12345678-1234-1234-1234-123456789abc', configuration_revision='a'*64,
            receipt_sha256='b'*64, volumes=[])
        if phase == 'retired':
            held.phase = 'retired'
            row.status = 'retired'
        row.record_json = held.model_dump_json()
        row.revision += 1
        row.record_digest = mod.record_digest(row)
        await session.commit()
    # Reopen a real SQLite session, as when reconciling after a restart/lost reply.
    async with factory() as session:
        row = await session.get(CapacityHold, original.id)
        assert native.request_for(row, fresh.observed.node.installation_id) == request
        assert native.root_accepted(row)
        # Normalizing lifecycle-only fields must not hide changed resource demand.
        changed = mod.commitment(row)
        changed.guests[0].memory_mib += 1
        row.record_json = changed.model_dump_json()
        row.record_digest = mod.record_digest(row)
        with pytest.raises(Range42Error):
            native.root_accepted(row)
