"""Deploy trigger — compose + preflight + runner spawn glue.

Ties together workspace lock acquisition, attempt_start event emission,
DetachedRunner (or FakeRunner under tests via RunnerProtocol), and the
EventsWatcher that translates ansible-runner job_events into the redacted
events.jsonl stream. attempt_end is emitted when the subprocess exits.

Spec refs: §7 runner lifecycle, §8 detached runner + watcher.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.allocation import ssh_controlmaster_env
from app.core.config import settings
from app.core.errors import ProjectCheckoutError
from app.core.events import EventsWriter
from app.core.events_watcher import EventsWatcher
from app.core.inventory_writer import write_inventory
from app.core.locks import acquire_lock
from app.core.logging import get_logger
from app.core.models import Attempt, Deployment, Project, ProxmoxHost, Source
from app.core.project import checkout_project
from app.core.redaction import (
    ConfigDenylistLayer,
    RedactionAuditWriter,
    TaintedStringLayer,
    VaultTaggedLayer,
)
from app.core.runner_detached import DetachedRunner
from app.core.runner_protocol import RunnerProtocol
from app.core.workspace import shred_envvars
from app.utils.checks_playbooks import resolve_scenarios_playbook

logger = get_logger(__name__)

_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


def _resolve_playbook_for_scenario(scenario_label: str) -> Path:
    """Resolve a scenario_label to its playbook path.

    Uses the existing checks_playbooks.resolve_scenarios_playbook() validator
    (regex: ``^[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*$``) so ``_universal`` is
    accepted and traversal attempts (e.g. ``../etc/passwd``) are rejected.

    :param scenario_label: The Deployment.scenario_label value.
    :type scenario_label: str
    :returns: Absolute path to ``<playbooks_dir>/scenarios/<scenario_label>/main.yml``.
    :rtype: Path
    :raises HTTPException: 400 if the label format is invalid, the file is
        missing, or a path traversal is detected.
    """
    return resolve_scenarios_playbook(scenario_label, playbooks_dir_type="www_app")


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

    # Resolve scenario_label -> playbook path so the runner (DetachedRunner
    # from T2-T4) can populate project/, env/cmdline, env/envvars from it.
    playbook_path = _resolve_playbook_for_scenario(dep.scenario_label)

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

    extravars: dict[str, Any] = {
        "r42_deployment_id": dep.id,
        "r42_attempt_id": attempt.id,
        "r42_scope": attempt.scope,
        "r42_team_id": attempt.team_id,
        "r42_playbook_path": str(playbook_path),
    }

    # Build the tainted-string set for substring redaction. Always includes
    # the vault password (when present); _universal scenarios additionally
    # add the Source PAT used for project clone.
    tainted: set[str] = set()
    if vault_pass.is_file():
        try:
            tainted.add(vault_pass.read_text().strip())
        except OSError:
            pass

    # Universal scenario: clone the project repo at the pinned project_sha and
    # render hosts.yml from the topology. Legacy scenarios (demo_lab, blank_*)
    # keep using the pre-rendered inventory at <ws>/inventory/ unchanged.
    if dep.scenario_label == "_universal":
        project = (await session.execute(
            select(Project).where(Project.id == dep.project_id))
        ).scalar_one()
        source = (await session.execute(
            select(Source).where(Source.id == project.source_id))
        ).scalar_one()
        target_host = (await session.execute(
            select(ProxmoxHost).where(ProxmoxHost.id == dep.target_host_id))
        ).scalar_one()

        # Guard against missing required fields (typed errors, not cryptic git failures)
        if not dep.project_sha:
            raise ProjectCheckoutError(
                message="project_sha is not set on this deployment; cannot clone for _universal scenario"
            )
        if not project.repo_owner or not project.repo_name:
            raise ProjectCheckoutError(
                message=f"project repo_owner/repo_name not set (got owner={project.repo_owner!r}, name={project.repo_name!r}); cannot construct clone URL"
            )

        # v1 simplification: source.token_ref is treated as the actual token
        # string (acknowledged debt — no secret store yet).
        if source.token_ref:
            tainted.add(str(source.token_ref))
        repo_url = (
            f"{source.base_url.rstrip('/')}/"
            f"{project.repo_owner}/{project.repo_name}.git"
        )
        project_dir = ws / "project"
        topology_path = checkout_project(
            repo_url=repo_url,
            sha=dep.project_sha,
            dest=project_dir,
            token=source.token_ref,
        )

        topology = json.loads(topology_path.read_text())

        # Extract Proxmox host/IP from api_url
        # (e.g. "https://192.168.1.10:8006" -> "192.168.1.10").
        api_url = str(target_host.api_url)
        if "://" in api_url:
            proxmox_address = api_url.split("://", 1)[1].split(":", 1)[0]
        else:
            proxmox_address = api_url.split(":", 1)[0]

        inventory_dir = ws / "inventory"
        inventory_dir.mkdir(parents=True, exist_ok=True)
        write_inventory(
            topology=topology,
            team_count=dep.team_count or 1,
            codename=dep.codename,
            proxmox_address=proxmox_address,
            ssh_keys_dir=ws / "ssh_keys",
            dest=inventory_dir / "hosts.yml",
        )

        extravars["r42_topology_path"] = str(topology_path)
        extravars["r42_inventory_dir"] = str(inventory_dir)
    else:
        # Legacy path: pre-rendered inventory (e.g. demo_lab) lives under
        # <ws>/inventory/; do not clone or generate anything.
        extravars["r42_inventory_dir"] = str(ws / "inventory")

    runner = runner or DetachedRunner()
    handle = await runner.start(
        private_data_dir=artifact_dir,
        extravars=extravars,
        envvars=envvars,
    )
    (artifact_dir / "pid").write_text(str(handle.pid or 0))

    layers = [ConfigDenylistLayer(settings.redaction_denylist),
              VaultTaggedLayer(),
              TaintedStringLayer(tainted_strings=tainted)]
    stop = asyncio.Event()
    watcher = EventsWatcher(
        job_events_dir=artifact_dir / "job_events",
        writer=writer, audit=audit, layers=layers,
        deployment_id=dep.id, attempt_id=attempt.id, stop=stop,
    )

    async def _run() -> None:
        task_watch = asyncio.create_task(watcher.run())
        try:
            rc = await handle.wait()
            stop.set()
            await task_watch
            writer.append({"event_type": "attempt_end",
                           "payload": {"terminal_state": "completed" if rc == 0 else "failed",
                                       "rc": rc}},
                          attempt_id=attempt.id, deployment_id=dep.id)
            logger.info("attempt finished", attempt_id=attempt.id, rc=rc)
        finally:
            # Shred secrets-bearing files in the runner's private_data_dir.
            # Defense-in-depth: prevents the snapshotted PAT / vault pass from
            # sitting on disk after the attempt completes. Runs on both success
            # and failure paths so cleanup is guaranteed.
            shred_envvars(artifact_dir / "env" / "envvars")
            shred_envvars(artifact_dir / "env" / "extravars")

    task = asyncio.create_task(_run())
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
