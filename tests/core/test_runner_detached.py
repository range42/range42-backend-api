import asyncio
import json
import os
import pytest
from pathlib import Path
from app.core.runner_detached import DetachedRunner


@pytest.mark.asyncio
async def test_detached_runner_writes_pid_and_exits(tmp_path, monkeypatch):
    # Stand in a shell command for the real ansible-runner binary to
    # validate start()/wait()/pid lifecycle.
    art = tmp_path / "art"
    script = tmp_path / "fake-runner.sh"
    script.write_text(
        "#!/bin/sh\n"
        "# $1 is the subcommand (start), $2 is the private_data_dir\n"
        "mkdir -p \"$2/job_events\"\n"
        "echo $$ > \"$2/pid\"\n"
        "echo 0 > \"$2/rc\"\n"
        "cat > \"$2/job_events/1-event.json\" <<JSON\n"
        '{"event": "runner_on_ok", "event_data": {"task": "hello"}}\n'
        "JSON\n"
    )
    script.chmod(0o755)

    runner = DetachedRunner(runner_bin=str(script))
    handle = await runner.start(private_data_dir=art, extravars={}, envvars={})
    rc = await handle.wait()
    assert rc == 0
    assert handle.pid is not None
    assert (art / "job_events" / "1-event.json").exists()


@pytest.mark.asyncio
async def test_detached_runner_kill(tmp_path):
    art = tmp_path / "art"
    art.mkdir(parents=True)
    sleeper = Path(tmp_path / "sleep.sh")
    sleeper.write_text(
        "#!/bin/sh\nmkdir -p \"$2\"; echo $$ > \"$2/pid\"; sleep 30\n"
    )
    sleeper.chmod(0o755)
    runner = DetachedRunner(runner_bin=str(sleeper))
    handle = await runner.start(private_data_dir=art, extravars={}, envvars={})
    await handle.kill()
    rc = await handle.wait()
    assert rc != 0
