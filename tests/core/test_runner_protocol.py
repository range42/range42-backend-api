import pytest
from tests.fixtures.fake_runner import FakeRunner
from app.core.runner_protocol import RunnerHandle


@pytest.mark.asyncio
async def test_fake_runner_emits_events_and_terminates(tmp_path):
    art = tmp_path / "artifacts"
    art.mkdir()
    runner = FakeRunner(script=[
        {"event_type": "attempt_start", "payload": {"scope": "full"}},
        {"event_type": "phase_transition", "payload": {"from": "pending", "to": "deploying"}},
        {"event_type": "task_end", "payload": {"task_name": "create_vm", "result": "ok"}},
        {"event_type": "attempt_end", "payload": {"terminal_state": "completed"}},
    ])
    handle: RunnerHandle = await runner.start(private_data_dir=art,
                                              extravars={}, envvars={})
    collected = []
    async for ev in handle.events():
        collected.append(ev)
    assert [e["event_type"] for e in collected] == [
        "attempt_start", "phase_transition", "task_end", "attempt_end"]
    assert handle.rc == 0
    assert handle.pid is not None
