"""Run Ansible in a separate session while tracking the actual playbook process.

The runner's foreground ``run`` command keeps its PID and exit status attached
to the handle. ``start_new_session`` separates it from the API's session, and
the persisted PID supports reconciliation after an API restart.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import tempfile
from pathlib import Path
from typing import Any, AsyncIterator

import yaml

from app.core.config import settings
from app.core.errors import RunnerSetupError
from app.core.logging import get_logger

logger = get_logger(__name__)


def _process_identity(pid: int) -> dict[str, int | str] | None:
    """Linux PID identity survives API restarts and rejects recycled PIDs."""
    if pid <= 0:
        return None
    try:
        # comm may contain spaces or parentheses; fields after its final ')'
        # begin with state (field 3), and starttime is field 22.
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            return None
        return {"pid": pid, "start_time": fields[19],
                "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip()}
    except (OSError, IndexError):
        return None


def record_process_identity(artifact_dir: Path, pid: int) -> bool:
    identity = _process_identity(pid)
    if identity is None:
        return False  # A very short-lived runner may already have exited.
    fd, temporary = tempfile.mkstemp(prefix=".process-", dir=artifact_dir)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(identity, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, artifact_dir / "process.json")
    finally:
        Path(temporary).unlink(missing_ok=True)
    return True


def process_matches(artifact_dir: Path, pid: int) -> bool:
    try:
        recorded = json.loads((artifact_dir / "process.json").read_text())
    except (OSError, ValueError):
        return False
    current = _process_identity(pid)
    return current is not None and recorded == current


class _LiteralCredential(str):
    """Credential data must never be evaluated as an Ansible template."""


class _ExtraVarsDumper(yaml.SafeDumper):
    """Keep the Ansible-specific tag local to runner extra variables."""


_ExtraVarsDumper.add_representer(
    _LiteralCredential,
    lambda dumper, value: dumper.represent_scalar("!unsafe", str(value)),
)


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

    Reads the per-attempt pidfile at ``<workspace>/runner/<attempt>/pid``
    (or the legacy runner pidfile) and sends SIGTERM (or SIGKILL for force).
    Returns True if a signal was sent, False if no pidfile or process.
    The events watcher then writes attempt_end with
    ``terminal_state=cancelled``.
    """
    ws = Path(workspace_path)
    pid_candidates = [
        ws / "runner" / "pid",
        ws / "runner" / "artifacts" / "pid",
    ]
    runner_dir = ws / "runner"
    if runner_dir.is_dir():
        pid_candidates.extend(sorted(runner_dir.glob("*/pid"), reverse=True))
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
        # A completed attempt's numeric PID can later belong to a different
        # process. Its recorded runner result means it must not be signalled.
        if (p.parent / "rc").is_file():
            continue
        try:
            pid = int(p.read_text().strip())
        except (FileNotFoundError, ValueError, OSError):
            continue
        if pid <= 0 or not process_matches(p.parent, pid):
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
        private_data_dir = Path(private_data_dir).resolve()
        private_data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        # ansible-runner appends ident to artifact-dir. Point that pair at
        # this attempt so the watcher reads job_events, rc and status here.
        argv = [
            self.runner_bin, "run", str(private_data_dir),
            "--artifact-dir", str(private_data_dir.parent),
            "--ident", private_data_dir.name,
        ]

        # Pinned projects supply an explicit root. Installed scenarios retain
        # the legacy root inference so relative imports/assets keep working.
        playbook_path_str = (extravars or {}).get("r42_playbook_path", "")
        if playbook_path_str:
            playbook_path = Path(playbook_path_str).resolve()
            if not playbook_path.is_file():
                raise RunnerSetupError(
                    message=f"r42_playbook_path missing or not a file: {playbook_path}"
                )

            explicit_root = (extravars or {}).get("r42_project_dir")
            if explicit_root:
                playbook_root = Path(explicit_root).resolve()
            else:
                scenarios = next(
                    (parent for parent in playbook_path.parents if parent.name == "scenarios"),
                    None,
                )
                playbook_root = scenarios.parent if scenarios else playbook_path.parent
            if not playbook_path.is_relative_to(playbook_root):
                raise RunnerSetupError(message="r42_playbook_path is outside the project directory")

            project_dir = private_data_dir / "project"
            if project_dir.exists() or project_dir.is_symlink():
                # Idempotent: same SHA -> same content; remove and re-link.
                if project_dir.is_symlink():
                    project_dir.unlink()
                else:
                    shutil.rmtree(project_dir)

            # Symlink for speed; ansible-runner is happy with this on Linux.
            project_dir.symlink_to(playbook_root, target_is_directory=True)

            playbook_rel = playbook_path.relative_to(playbook_root)
            argv.extend(["--playbook", str(playbook_rel)])

            inventory_path_str = (extravars or {}).get("r42_inventory_path")
            inventory_dir_str = (extravars or {}).get("r42_inventory_dir")
            if inventory_path_str:
                inventory_src = Path(inventory_path_str).resolve()
                if not inventory_src.is_file():
                    raise RunnerSetupError(
                        message=f"r42_inventory_path missing or not a file: {inventory_src}"
                    )
                argv.extend(["--inventory", str(inventory_src)])
            elif inventory_dir_str:
                inventory_src = Path(inventory_dir_str).resolve()
                if not inventory_src.is_dir():
                    raise RunnerSetupError(
                        message=(
                            "r42_inventory_dir does not exist or is not a "
                            f"directory: {inventory_src}"
                        )
                    )
                inventory_dir = private_data_dir / "inventory"
                if inventory_dir.exists() or inventory_dir.is_symlink():
                    if inventory_dir.is_symlink():
                        inventory_dir.unlink()
                    elif inventory_dir.is_dir():
                        shutil.rmtree(inventory_dir)
                inventory_dir.symlink_to(inventory_src, target_is_directory=True)
                argv.extend(["--inventory", str(inventory_dir)])

        env_dir = private_data_dir / "env"
        env_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Old attempts incorrectly put runner's -p option in Ansible's
        # cmdline file. Remove it when preparing a reused private directory.
        (env_dir / "cmdline").unlink(missing_ok=True)
        extravars_path = env_dir / "extravars"
        prepared_vars = dict(extravars or {})
        for key in ("default_admin_vm_ci_password", "proxmox_api_token_secret"):
            value = prepared_vars.get(key)
            if isinstance(value, str):
                prepared_vars[key] = _LiteralCredential(value)
        # Runner passes this file directly to Ansible with -e @env/extravars.
        # Only resolved credentials are literal; normal variables may template.
        extravars_path.write_text(yaml.dump(prepared_vars, Dumper=_ExtraVarsDumper))
        extravars_path.chmod(0o600)

        # ArtifactLoader expects a YAML mapping; JSON preserves strings and
        # multiline values and is accepted by that loader.
        envvars_path = env_dir / "envvars"
        prepared_env = {k: str(v) for k, v in (envvars or {}).items()}
        envvars_path.write_text(json.dumps(prepared_env))
        envvars_path.chmod(0o600)

        merged_env = dict(os.environ)
        merged_env.update(prepared_env)

        logger.info("spawning runner", bin=self.runner_bin, dir=str(private_data_dir))
        lock_fd = prepared_env.get("RANGE42_PROVISIONING_LOCK_FD")
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=str(private_data_dir),
            env=merged_env,
            start_new_session=True,
            pass_fds=(int(lock_fd),) if lock_fd is not None else (),
        )
        (private_data_dir / "pid").write_text(str(proc.pid))
        record_process_identity(private_data_dir, proc.pid)
        return _SubprocessHandle(private_data_dir, proc)
