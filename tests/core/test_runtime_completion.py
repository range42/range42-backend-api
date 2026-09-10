"""Runtime success is established from readback, including partial operations."""
import pytest
import yaml
from tests.core.test_attempt_lifecycle import lifecycle_db as lifecycle_db

from tests.core.test_runtime_operations import state


@pytest.mark.parametrize("matched", [False, True])
def test_firewall_result_requires_switch_and_every_requested_nic(matched):
    from app.core.runtime_completion import assess_runtime_result
    current = state()
    current["vms"][0]["firewall_enabled"] = True
    current["vms"][0]["nics"][0]["firewall_enabled"] = matched
    result = assess_runtime_result({"kind": "vm_firewall", "vm_id": 3191, "enabled": True},
                                   {"vmids": [3191], "missing_vmids": []}, current, [])
    assert result["desired_reached"] is matched
    assert result["matched_vmids"] == ([3191] if matched else [])


def test_partial_sweep_retains_missing_and_failed_guest_results():
    from app.core.runtime_completion import assess_runtime_result
    current = state()
    current["vms"][0]["firewall_enabled"] = True
    current["vms"][0]["nics"][0]["firewall_enabled"] = True
    current["vms"].append({"vm_id": 3192, "status": "conflict"})
    result = assess_runtime_result({"kind": "scenario_firewall", "enabled": True},
                                   {"vmids": [3191, 3192], "missing_vmids": [3193]}, current, [])
    assert result["desired_reached"] is False
    assert result["partial"] is True
    assert result["matched_vmids"] == [3191]
    assert result["mismatched_vmids"] == [3192]
    assert result["missing_vmids"] == [3193]


@pytest.mark.parametrize("count,expected", [(None, False), (0, False), (1, True), (2, False)])
def test_nat_success_requires_live_rule_readback_as_well_as_declaration(count, expected):
    from app.core.runtime_completion import assess_runtime_result
    current = state()
    current["networks"][0]["configured_snat"] = True
    events = [] if count is None else [{"payload": {"res": {"network_delete_extra_snat_rules": {
        "subnet_cidr": "10.42.70.0/24", "snat_after": count, "snat_host": "r42-proxmox-cli",
    }}}}]
    result = assess_runtime_result({"kind": "sdn_snat", "enabled": True, "vnet": "r42blue"},
                                   {"subnet": "10.42.70.0/24"}, current, events)
    assert result["desired_reached"] is expected
    assert result["live_snat_rule_count"] == count
    assert result["live_forwarding_verified"] is False


@pytest.mark.asyncio
async def test_verified_partial_result_persists_without_becoming_success(lifecycle_db, monkeypatch):
    from app.core import attempt_lifecycle
    from app.core.models import Attempt
    monkeypatch.setattr(attempt_lifecycle, "get_session_factory", lambda: lifecycle_db)
    result = await attempt_lifecycle.finish_attempt(attempt_id="att", rc=0, partial=True)
    assert result == "partial"
    async with lifecycle_db() as session:
        assert (await session.get(Attempt, "att")).state == "partial"


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [False, True])
async def test_recovered_operation_persists_readback_before_terminal_event(lifecycle_db, tmp_path, monkeypatch, missing):
    from app.core import attempt_lifecycle, orphans, runtime_completion
    from app.core.events import EventsReader, EventsWriter
    from app.core.models import Attempt, Deployment
    monkeypatch.setattr(attempt_lifecycle, "get_session_factory", lambda: lifecycle_db)
    monkeypatch.setattr(runtime_completion, "get_session_factory", lambda: lifecycle_db)
    current = state()
    current["vms"][0]["firewall_enabled"] = True
    current["vms"][0]["nics"][0]["firewall_enabled"] = True

    async def observed(*args, **kwargs):
        return current

    monkeypatch.setattr(runtime_completion, "read_runtime_state", observed)
    artifact = tmp_path / "runner/att"
    (artifact / "runtime").mkdir(parents=True)
    (artifact / "checkout/scenarios/content").mkdir(parents=True)
    (artifact / "runtime/context.yml").write_text(yaml.safe_dump({
        "scenario_dir": "checkout/scenarios/content",
        "plan": {"vmids": [3191], "missing_vmids": [3192] if missing else []},
    }))
    EventsWriter(tmp_path / "events.jsonl").append({"event_type": "attempt_start"}, attempt_id="att")
    async with lifecycle_db() as session:
        attempt = await session.get(Attempt, "att")
        attempt.scope, attempt.state = "runtime", "deploying"
        attempt.operation = {"request": {"kind": "scenario_firewall", "enabled": True}}
        dep = await session.get(Deployment, "dep")
        await session.commit()
    await orphans._finish_recovered(dep, attempt, artifact, 0)
    async with lifecycle_db() as session:
        attempt = await session.get(Attempt, "att")
        assert attempt.state == ("partial" if missing else "succeeded")
        assert attempt.operation_result["desired_reached"] is (not missing)
        events = list(EventsReader(tmp_path / "events.jsonl").read_range())
        assert events[-2]["payload"]["runtime_result"] == attempt.operation_result
        assert events[-1]["event_type"] == "attempt_end"
        assert attempt.event_cursor_tip == events[-1]["event_seq"]
