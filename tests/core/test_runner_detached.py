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


@pytest.mark.asyncio
async def test_detached_runner_writes_cmdline(tmp_path: Path, monkeypatch):
    playbook_root = tmp_path / "playbooks"
    (playbook_root / "scenarios" / "_universal").mkdir(parents=True)
    pb = playbook_root / "scenarios" / "_universal" / "main.yml"
    pb.write_text("- hosts: all\n  tasks: []\n")

    private_data_dir = tmp_path / "pdd"
    private_data_dir.mkdir()

    # Stub the subprocess spawn so the test runs offline (same pattern as
    # test_detached_runner_writes_project_dir).
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

    await runner.start(
        private_data_dir=private_data_dir,
        extravars={
            "r42_playbook_path": str(pb),
            "r42_inventory_dir": str(tmp_path / "inv"),
        },
        envvars={},
    )

    cmdline = (private_data_dir / "env" / "cmdline").read_text()
    assert "-p scenarios/_universal/main.yml" in cmdline
    assert "-i inventory" in cmdline


@pytest.mark.asyncio
async def test_detached_runner_envvars_key_value_format(tmp_path: Path, monkeypatch):
    playbook_root = tmp_path / "playbooks"
    (playbook_root / "scenarios" / "x").mkdir(parents=True)
    pb = playbook_root / "scenarios" / "x" / "main.yml"
    pb.write_text("- hosts: all\n  tasks: []\n")

    private_data_dir = tmp_path / "pdd"
    private_data_dir.mkdir()

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

    await runner.start(
        private_data_dir=private_data_dir,
        extravars={"r42_playbook_path": str(pb), "r42_inventory_dir": str(tmp_path / "inv")},
        envvars={"FOO": "bar", "QUX": "with spaces"},
    )

    envvars_path = private_data_dir / "env" / "envvars"
    content = envvars_path.read_text()

    # KEY=VALUE lines (one per line), not JSON
    assert "FOO=bar\n" in content
    assert "QUX=with spaces\n" in content
    assert not content.startswith("{")  # not JSON

    # Permissions are 0600
    mode = envvars_path.stat().st_mode & 0o777
    assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"


@pytest.mark.asyncio
async def test_detached_runner_extravars_perms_0600(tmp_path: Path, monkeypatch):
    """env/extravars must also be 0600 (it can carry vault passwords / tokens via extravars)."""
    playbook_root = tmp_path / "playbooks"
    (playbook_root / "scenarios" / "x").mkdir(parents=True)
    pb = playbook_root / "scenarios" / "x" / "main.yml"
    pb.write_text("- hosts: all\n  tasks: []\n")
    private_data_dir = tmp_path / "pdd"
    private_data_dir.mkdir()

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

    await runner.start(
        private_data_dir=private_data_dir,
        extravars={"r42_playbook_path": str(pb), "r42_inventory_dir": str(tmp_path / "inv")},
        envvars={},
    )

    extravars_path = private_data_dir / "env" / "extravars"
    mode = extravars_path.stat().st_mode & 0o777
    assert mode == 0o600, f"Expected 0o600 on extravars, got {oct(mode)}"


@pytest.mark.asyncio
async def test_detached_runner_envvars_rejects_newline_in_value(tmp_path: Path, monkeypatch):
    """KEY=VALUE format can't safely encode newlines; reject explicitly."""
    from app.core.errors import RunnerSetupError
    playbook_root = tmp_path / "playbooks"
    (playbook_root / "scenarios" / "x").mkdir(parents=True)
    pb = playbook_root / "scenarios" / "x" / "main.yml"
    pb.write_text("- hosts: all\n  tasks: []\n")
    private_data_dir = tmp_path / "pdd"
    private_data_dir.mkdir()

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

    with pytest.raises(RunnerSetupError, match="newline"):
        await runner.start(
            private_data_dir=private_data_dir,
            extravars={"r42_playbook_path": str(pb), "r42_inventory_dir": str(tmp_path / "inv")},
            envvars={"BAD": "line1\nline2"},
        )
