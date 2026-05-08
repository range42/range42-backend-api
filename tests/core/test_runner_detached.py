import asyncio
import json
import os
import pytest
from pathlib import Path
from app.core import runner_detached as runner_detached_module
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


@pytest.mark.asyncio
async def test_detached_runner_writes_project_dir(tmp_path: Path, monkeypatch):
    """DetachedRunner.start() must populate private_data_dir/project/ with the playbook tree."""
    # Arrange: a fake playbook tree rooted under <playbook_root>/scenarios/_universal
    playbook_root = tmp_path / "playbooks"
    playbook_root.mkdir()
    (playbook_root / "scenarios").mkdir()
    (playbook_root / "scenarios" / "_universal").mkdir()
    (playbook_root / "scenarios" / "_universal" / "main.yml").write_text(
        "- hosts: all\n  tasks: []\n"
    )

    private_data_dir = tmp_path / "pdd"
    private_data_dir.mkdir()

    # Stub the subprocess spawn so the test runs offline (no real ansible-runner).
    class _FakeProc:
        pid = 12345
        returncode = 0

        async def wait(self):
            return 0

    async def _fake_spawn(*args, **kwargs):
        return _FakeProc()

    monkeypatch.setattr(
        runner_detached_module.asyncio,
        "create_subprocess_exec",
        _fake_spawn,
    )

    runner = DetachedRunner()

    # Act
    await runner.start(
        private_data_dir=private_data_dir,
        extravars={
            "r42_playbook_path": str(
                playbook_root / "scenarios" / "_universal" / "main.yml"
            ),
            "r42_inventory_dir": str(tmp_path / "inv"),
        },
        envvars={},
    )

    # Assert: project/ tree was populated and the playbook is reachable
    assert (
        private_data_dir / "project" / "scenarios" / "_universal" / "main.yml"
    ).exists(), "DetachedRunner did not copy/symlink the playbook tree into project/"
