"""Detached ansible-runner CLI subprocess wrapper.

Spawns ansible-runner via asyncio.create_subprocess_exec and returns a
handle whose events stream is driven by the EventsWatcher (Task 21)
tailing the artifact_dir/job_events/ directory.

The subprocess daemonises via ansible-runner CLI behaviour; FastAPI can
exit without killing the run. Reconcile-on-startup (Task 22) re-adopts
live pids after restart.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
from pathlib import Path
from typing import Any, AsyncIterator

from app.core.config import settings
from app.core.errors import RunnerSetupError
from app.core.logging import get_logger

logger = get_logger(__name__)


class _SubprocessHandle:
    def __init__(self, artifact_dir: Path, proc: asyncio.subprocess.Process) -> None:
        self.artifact_dir = Path(artifact_dir)
        self._proc = proc
        self.pid: int | None = proc.pid
        self.rc: int | None = None

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        # Real event streaming goes through EventsWatcher; this iterator
        # yields from the artifact dir's job_events subdir as a fallback.
        seen: set[str] = set()
        while True:
            await asyncio.sleep(0.2)
            je = self.artifact_dir / "job_events"
            if je.exists():
                for p in sorted(je.glob("*.json")):
                    if p.name in seen:
                        continue
                    seen.add(p.name)
                    try:
                        yield json.loads(p.read_text())
                    except ValueError:
                        continue
            if self._proc.returncode is not None:
                break

    async def wait(self) -> int:
        rc = await self._proc.wait()
        self.rc = rc
        return rc

    async def kill(self) -> None:
        if self._proc.returncode is None:
            try:
                os.kill(self._proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                try:
                    os.kill(self._proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


async def signal_running_attempt(workspace_path: str | Path, *,
                                 signal: str = "SIGTERM") -> bool:
    """Signal the detached ansible-runner subprocess for a workspace.

    Reads the pidfile produced by ansible-runner at
    ``<workspace>/runner/pid`` and sends SIGTERM (or SIGKILL for force).
    Returns True if a signal was sent, False if no pidfile or process.
    The events watcher then writes attempt_end with
    ``terminal_state=cancelled``.
    """
    ws = Path(workspace_path)
    pid_candidates = [
        ws / "runner" / "pid",
        ws / "runner" / "artifacts" / "pid",
    ]
    # Fall back: look inside any artifact dir.
    artifacts_dir = ws / "runner" / "artifacts"
    if artifacts_dir.exists():
        for child in artifacts_dir.iterdir():
            pid_candidates.append(child / "pid")
    sig = getattr(__import__("signal"), signal, None)
    if sig is None:
        logger.warning("unknown_signal", signal=signal)
        return False
    for p in pid_candidates:
        try:
            pid = int(p.read_text().strip())
        except (FileNotFoundError, ValueError, OSError):
            continue
        try:
            os.kill(pid, sig)
            logger.info("signalled_attempt", pid=pid, signal=signal)
            return True
        except ProcessLookupError:
            continue
    return False


class DetachedRunner:
    def __init__(self, runner_bin: str | None = None) -> None:
        self.runner_bin = runner_bin or settings.runner_bin

    async def start(self, *, private_data_dir: Path,
                    extravars: dict[str, Any],
                    envvars: dict[str, str]) -> _SubprocessHandle:
        private_data_dir = Path(private_data_dir)
        private_data_dir.mkdir(parents=True, exist_ok=True)

        # Populate project/ with the playbook tree so ansible-runner can
        # resolve the playbook by relative path. We resolve the playbook
        # root by walking up from r42_playbook_path until we find the
        # directory whose child is `scenarios/` (the playbooks repo root).
        playbook_path_str = (extravars or {}).get("r42_playbook_path", "")
        if playbook_path_str:
            playbook_path = Path(playbook_path_str)
            if not playbook_path.is_file():
                raise RunnerSetupError(
                    message=f"r42_playbook_path missing or not a file: {playbook_path}"
                )

            playbook_root = playbook_path.parent
            while playbook_root.parent.name and playbook_root.parent.name != "scenarios":
                playbook_root = playbook_root.parent
            if playbook_root.parent.name == "scenarios":
                playbook_root = playbook_root.parent.parent

            project_dir = private_data_dir / "project"
            if project_dir.exists() or project_dir.is_symlink():
                # Idempotent: same SHA -> same content; remove and re-link.
                if project_dir.is_symlink():
                    project_dir.unlink()
                else:
                    shutil.rmtree(project_dir)

            # Symlink for speed; ansible-runner is happy with this on Linux.
            project_dir.symlink_to(playbook_root, target_is_directory=True)

            # Compute the playbook's path relative to the project root so
            # ansible-runner can locate it via -p <relative path>.
            playbook_rel = playbook_path.relative_to(playbook_root)

            env_dir = private_data_dir / "env"
            env_dir.mkdir(exist_ok=True, mode=0o700)

            cmdline_path = env_dir / "cmdline"
            cmdline_path.write_text(f"-p {playbook_rel} -i inventory\n")
            cmdline_path.chmod(0o600)

        env_dir = private_data_dir / "env"
        env_dir.mkdir(parents=True, exist_ok=True)
        (env_dir / "extravars").write_text(json.dumps(extravars or {}))
        env_file = env_dir / "envvars"
        env_file.write_text(json.dumps(envvars or {}))

        merged_env = dict(os.environ)
        merged_env.update({k: str(v) for k, v in (envvars or {}).items()})

        logger.info("spawning runner", bin=self.runner_bin, dir=str(private_data_dir))
        # NOTE: "start" is required as argv[1] because the ansible-runner
        # CLI dispatches by subcommand. Without it the binary errors with
        # "ansible-runner: error: argument action: invalid choice".
        proc = await asyncio.create_subprocess_exec(
            self.runner_bin, "start", str(private_data_dir),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=str(private_data_dir),
            env=merged_env,
            start_new_session=True,
        )
        return _SubprocessHandle(private_data_dir, proc)
