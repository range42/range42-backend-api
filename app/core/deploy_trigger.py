"""Deploy trigger — compose + preflight + runner spawn glue.

Ties together workspace lock acquisition, attempt_start event emission,
DetachedRunner (or FakeRunner under tests via RunnerProtocol), and the
EventsWatcher that translates ansible-runner job_events into the redacted
events.jsonl stream. attempt_end is emitted when the subprocess exits.

Spec refs: §7 runner lifecycle, §8 detached runner + watcher.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.allocation import ssh_controlmaster_env
from app.core.config import settings
from app.core.events import EventsWriter
from app.core.events_watcher import EventsWatcher
from app.core.locks import acquire_lock
from app.core.logging import get_logger
from app.core.models import Attempt, Deployment
from app.core.redaction import (
    ConfigDenylistLayer,
    RedactionAuditWriter,
    VaultTaggedLayer,
)
from app.core.runner_detached import DetachedRunner
from app.core.runner_protocol import RunnerProtocol

logger = get_logger(__name__)

_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


async def start_attempt(session: AsyncSession, *, attempt: Attempt,
                        runner: RunnerProtocol | None = None) -> None:
    """Spawn the detached runner for an attempt and begin event watching.

    Acquires the workspace lock, writes an attempt_start event, starts
    the runner subprocess, and spawns the EventsWatcher as a background
    asyncio Task. Returns immediately — attempt lifecycle runs async.
    """
    dep = (await session.execute(
        select(Deployment).where(Deployment.id == attempt.deployment_id))
    ).scalar_one()
    ws = Path(dep.workspace_path)
    events_jsonl = ws / "events.jsonl"
    redactions_jsonl = ws / "redactions.jsonl"
    artifact_dir = ws / "runner" / attempt.id
    artifact_dir.mkdir(parents=True, exist_ok=True)

    await acquire_lock(session, deployment_id=dep.id,
                       owner=f"attempt-{attempt.id}", interval_s=30)
    await session.commit()

    writer = EventsWriter(events_jsonl)
    audit = RedactionAuditWriter(redactions_jsonl)
    writer.append({"event_type": "attempt_start",
                   "payload": {"scope": attempt.scope, "team_id": attempt.team_id}},
                  attempt_id=attempt.id, deployment_id=dep.id)

    envvars: dict[str, str] = {
        "RANGE42_TRACE_ID": attempt.id,
        "ANSIBLE_FORKS": "25",
        "ANSIBLE_PIPELINING": "True",
    }
    envvars.update(ssh_controlmaster_env(deployment_id=dep.id))
    vault_pass = ws / "secrets" / "vault_pass.txt"
    if vault_pass.exists():
        envvars["ANSIBLE_VAULT_PASSWORD_FILE"] = str(vault_pass)

    runner = runner or DetachedRunner()
    handle = await runner.start(
        private_data_dir=artifact_dir,
        extravars={"r42_deployment_id": dep.id,
                   "r42_attempt_id": attempt.id,
                   "r42_scope": attempt.scope,
                   "r42_team_id": attempt.team_id},
        envvars=envvars,
    )
    (artifact_dir / "pid").write_text(str(handle.pid or 0))

    layers = [ConfigDenylistLayer(settings.redaction_denylist),
              VaultTaggedLayer()]
    stop = asyncio.Event()
    watcher = EventsWatcher(
        job_events_dir=artifact_dir / "job_events",
        writer=writer, audit=audit, layers=layers,
        deployment_id=dep.id, attempt_id=attempt.id, stop=stop,
    )

    async def _run() -> None:
        task_watch = asyncio.create_task(watcher.run())
        rc = await handle.wait()
        stop.set()
        await task_watch
        writer.append({"event_type": "attempt_end",
                       "payload": {"terminal_state": "completed" if rc == 0 else "failed",
                                   "rc": rc}},
                      attempt_id=attempt.id, deployment_id=dep.id)
        logger.info("attempt finished", attempt_id=attempt.id, rc=rc)

    task = asyncio.create_task(_run())
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
