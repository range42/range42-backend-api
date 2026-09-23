import json

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
        "# $1 is the subcommand (run), $2 is the private_data_dir\n"
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
    (tmp_path / "inv").mkdir()

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
async def test_detached_runner_passes_runner_options_as_argv(tmp_path: Path, monkeypatch):
    playbook_root = tmp_path / "playbooks"
    (playbook_root / "scenarios" / "_universal").mkdir(parents=True)
    pb = playbook_root / "scenarios" / "_universal" / "main.yml"
    pb.write_text("- hosts: all\n  tasks: []\n")

    private_data_dir = tmp_path / "pdd"
    private_data_dir.mkdir()
    (tmp_path / "inv").mkdir()

    # Stub the subprocess spawn so the test runs offline (same pattern as
    # test_detached_runner_writes_project_dir).
    class _FakeProc:
        pid = 12345
        returncode = 0

        async def wait(self):
            return 0

    calls = []

    async def _fake_spawn(*args, **kwargs):
        calls.append((args, kwargs))
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

    argv, kwargs = calls[0]
    assert argv[1:3] == ("run", str(private_data_dir))
    assert argv[argv.index("--playbook") + 1] == "scenarios/_universal/main.yml"
    assert argv[argv.index("--inventory") + 1] == str(private_data_dir / "inventory")
    assert argv[argv.index("--artifact-dir") + 1] == str(private_data_dir.parent)
    assert argv[argv.index("--ident") + 1] == private_data_dir.name
    assert kwargs["start_new_session"] is True
    assert not (private_data_dir / "env" / "cmdline").exists()


@pytest.mark.asyncio
async def test_detached_runner_envvars_are_a_yaml_mapping(tmp_path: Path, monkeypatch):
    playbook_root = tmp_path / "playbooks"
    (playbook_root / "scenarios" / "x").mkdir(parents=True)
    pb = playbook_root / "scenarios" / "x" / "main.yml"
    pb.write_text("- hosts: all\n  tasks: []\n")

    private_data_dir = tmp_path / "pdd"
    private_data_dir.mkdir()
    (tmp_path / "inv").mkdir()

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

    # JSON is a YAML mapping, as required by ansible-runner's ArtifactLoader.
    assert json.loads(content) == {"FOO": "bar", "QUX": "with spaces"}

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
    (tmp_path / "inv").mkdir()

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
async def test_detached_runner_envvars_round_trip_multiline_values(tmp_path: Path, monkeypatch):
    """A YAML mapping preserves multiline values without injecting new keys."""
    playbook_root = tmp_path / "playbooks"
    (playbook_root / "scenarios" / "x").mkdir(parents=True)
    pb = playbook_root / "scenarios" / "x" / "main.yml"
    pb.write_text("- hosts: all\n  tasks: []\n")
    private_data_dir = tmp_path / "pdd"
    private_data_dir.mkdir()
    (tmp_path / "inv").mkdir()

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
        envvars={"MULTILINE": "line1\nline2"},
    )
    assert json.loads((private_data_dir / "env" / "envvars").read_text()) == {
        "MULTILINE": "line1\nline2",
    }


@pytest.mark.asyncio
async def test_detached_runner_writes_inventory_symlink(tmp_path: Path, monkeypatch):
    """DetachedRunner must symlink inventory/ from r42_inventory_dir extravar.

    Legacy deployments supply an inventory directory. It remains reachable
    through the runner's inventory symlink and explicit --inventory argument.
    """
    playbook_root = tmp_path / "playbooks"
    (playbook_root / "scenarios" / "x").mkdir(parents=True)
    pb = playbook_root / "scenarios" / "x" / "main.yml"
    pb.write_text("- hosts: all\n  tasks: []\n")

    inv_src = tmp_path / "ws" / "inventory"
    inv_src.mkdir(parents=True)
    (inv_src / "hosts.yml").write_text(
        "all:\n  hosts:\n    fake:\n      ansible_host: 1.2.3.4\n"
    )

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
        extravars={
            "r42_playbook_path": str(pb),
            "r42_inventory_dir": str(inv_src),
        },
        envvars={},
    )

    inv_link = private_data_dir / "inventory"
    assert inv_link.exists(), "inventory/ subdir not created"
    assert inv_link.is_symlink() or inv_link.is_dir(), \
        "inventory/ should be a symlink or directory"
    # The inventory argument must find hosts.yml.
    assert (inv_link / "hosts.yml").is_file()


@pytest.mark.asyncio
async def test_detached_runner_raises_when_inventory_dir_missing(
    tmp_path: Path, monkeypatch
):
    """Missing r42_inventory_dir should produce a typed error early,
    not a confusing later failure when ansible-runner can't find inventory.
    """
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

    with pytest.raises(RunnerSetupError, match="r42_inventory_dir"):
        await runner.start(
            private_data_dir=private_data_dir,
            extravars={
                "r42_playbook_path": str(pb),
                "r42_inventory_dir": str(tmp_path / "missing"),
            },
            envvars={},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("symlink_escape", [False, True])
async def test_detached_runner_rejects_playbook_outside_explicit_project(
    tmp_path, symlink_escape,
):
    from app.core.errors import RunnerSetupError

    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.yml"
    outside.write_text("- hosts: all\n  tasks: []\n")
    playbook = outside
    if symlink_escape:
        playbook = project / "main.yml"
        playbook.symlink_to(outside)

    with pytest.raises(RunnerSetupError, match="outside.*project"):
        await DetachedRunner().start(
            private_data_dir=tmp_path / "attempt",
            extravars={"r42_project_dir": str(project), "r42_playbook_path": str(playbook)},
            envvars={},
        )


@pytest.mark.asyncio
async def test_signal_running_attempt_finds_per_attempt_pid(tmp_path, monkeypatch):
    attempt = tmp_path / "runner" / "attempt-123"
    attempt.mkdir(parents=True)
    (attempt / "pid").write_text("12345")
    calls = []
    monkeypatch.setattr(runner_detached_module.os, "kill", lambda pid, sig: calls.append((pid, sig)))
    monkeypatch.setattr(runner_detached_module, "process_matches", lambda path, pid: True)

    assert await runner_detached_module.signal_running_attempt(tmp_path)
    assert calls == [(12345, runner_detached_module.signal.SIGTERM)]


@pytest.mark.asyncio
async def test_signal_does_not_trust_a_pid_without_process_identity(tmp_path, monkeypatch):
    artifact = tmp_path / "runner" / "attempt"
    artifact.mkdir(parents=True)
    (artifact / "pid").write_text("12345")
    calls = []
    monkeypatch.setattr(runner_detached_module.os, "kill", lambda *args: calls.append(args))
    assert not await runner_detached_module.signal_running_attempt(tmp_path)
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("pid", ["0", "-1"])
async def test_signal_running_attempt_ignores_nonpositive_pid(tmp_path, monkeypatch, pid):
    runner = tmp_path / "runner"
    runner.mkdir()
    (runner / "pid").write_text(pid)
    calls = []
    monkeypatch.setattr(runner_detached_module.os, "kill", lambda *args: calls.append(args))

    assert not await runner_detached_module.signal_running_attempt(tmp_path)
    assert not calls


@pytest.mark.asyncio
async def test_signal_running_attempt_skips_completed_attempt_pid(tmp_path, monkeypatch):
    runner = tmp_path / "runner"
    completed = runner / "completed-attempt"
    completed.mkdir(parents=True)
    (completed / "pid").write_text("12345")
    (completed / "rc").write_text("0")
    calls = []
    monkeypatch.setattr(runner_detached_module.os, "kill", lambda *args: calls.append(args))

    assert not await runner_detached_module.signal_running_attempt(tmp_path)
    assert not calls
