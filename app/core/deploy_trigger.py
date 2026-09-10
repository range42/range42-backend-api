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
import os
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.allocation import ssh_controlmaster_env
from app.core.attempt_lifecycle import finish_attempt, keep_attempt_lock, mark_attempt_running
from app.core.config import settings
from app.core.errors import PreflightBlockedError, Range42Error
from app.core.events import EventsWriter
from app.core.events_watcher import EventsWatcher
from app.core.locks import ProvisioningLock, acquire_lock
from app.core.logging import get_logger
from app.core.models import Attempt, Deployment, Project, ProxmoxHost, Source
from app.core.orphans import track_attempt, untrack_attempt
from app.core.preflight import check_vmids
from app.core.scenario import prepare_project_scenario, validate_concrete_scope
from app.core.scenario_networks import check_scenario_networks
from app.core.scenario_resources import check_scenario_resources
from app.core.scenario_runtime import cleanup_runtime_vault, prepare_runtime_vault, target_runtime_variables
from app.core.redaction import (
    ConfigDenylistLayer,
    RedactionAuditWriter,
    TaintedStringLayer,
    VaultTaggedLayer,
)
from app.core.runner_detached import DetachedRunner
from app.core.runner_protocol import RunnerProtocol
from app.core.ssh_agent import unlock_workspace_keys
from app.core.workspace import shred_envvars
from app.utils.checks_playbooks import resolve_scenarios_playbook

logger = get_logger(__name__)

_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


def _resolve_playbook_for_scenario(scenario_label: str) -> Path:
    """Resolve a scenario_label to its playbook path.

    Uses the existing checks_playbooks.resolve_scenarios_playbook() validator
    so retired scenarios and traversal attempts are rejected.

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
    task = asyncio.current_task()
    track_attempt(attempt.id, task)
    try:
        dep = await session.get(Deployment, attempt.deployment_id)
        validate_concrete_scope(dep, attempt.scope)
        if dep.project_sha and attempt.scope == "full":
            root = Path(os.getenv("RANGE42_WORKSPACE_ROOT", str(settings.workspace_root)))
            with ProvisioningLock(root / ".locks") as lock:
                await _start_attempt(session, attempt=attempt, runner=runner, provisioning_fd=lock.fd)
        else:
            await _start_attempt(session, attempt=attempt, runner=runner)
    finally:
        untrack_attempt(attempt.id, task)


async def _start_attempt(session: AsyncSession, *, attempt: Attempt,
                         runner: RunnerProtocol | None = None,
                         provisioning_fd: int | None = None) -> None:
    """Spawn the detached runner for an attempt and begin event watching.

    Acquires the workspace lock, writes an attempt_start event, starts
    the runner subprocess, and spawns the EventsWatcher as a background
    asyncio Task. Returns immediately — attempt lifecycle runs async.
    """
    dep = (await session.execute(
        select(Deployment).where(Deployment.id == attempt.deployment_id))
    ).scalar_one()
    validate_concrete_scope(dep, attempt.scope)
    if attempt.scope != "full" and not dep.project_sha:
        raise Range42Error(
            code="PROJECT_SCENARIO_SCOPE_UNSUPPORTED",
            error="unsupported_scope",
            message="This operation requires a pinned concrete scenario with an explicit entrypoint",
        )
    ws = Path(dep.workspace_path)
    events_jsonl = ws / "events.jsonl"
    redactions_jsonl = ws / "redactions.jsonl"
    artifact_dir = ws / "runner" / attempt.id
    artifact_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    artifact_dir.chmod(0o700)

    await acquire_lock(session, deployment_id=dep.id,
                       owner=f"attempt-{attempt.id}", interval_s=30)
    await session.commit()

    # Resolve scenario_label -> playbook path so the runner (DetachedRunner
    # from T2-T4) can populate project/, env/cmdline, env/envvars from it.
    scenario = None
    if dep.project_sha:
        scenario = await prepare_project_scenario(
            session, dep, dest=artifact_dir / "checkout", scope=attempt.scope,
            project_sha=attempt.project_sha if attempt.scope == "configure" else None,
        )
        target_host = await session.get(ProxmoxHost, dep.target_host_id)
        overrides = json.loads(target_host.protected_vmids_override_json) if (
            target_host and target_host.protected_vmids_override_json
        ) else None
        vmid_check = check_vmids(scenario.vmids, host_overrides=overrides)
        if vmid_check.result == "block":
            raise PreflightBlockedError(message=vmid_check.detail)
        network_checks = await check_scenario_networks(scenario.playbook.parent, target_host, scope=attempt.scope)
        blocked = next((check for check in network_checks if check.result == "block"), None)
        if blocked:
            raise PreflightBlockedError(message=blocked.detail)
        resource_checks = await check_scenario_resources(
            scenario.playbook.parent, target_host, deployment_id=dep.id, scope=attempt.scope,
        )
        blocked = next((check for check in resource_checks if check.result == "block"), None)
        if blocked:
            raise PreflightBlockedError(message=blocked.detail)
        playbook_path = scenario.playbook
    else:
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
    if provisioning_fd is not None:
        envvars["RANGE42_PROVISIONING_LOCK_FD"] = str(provisioning_fd)
    vault_pass = ws / "secrets" / "vault_pass.txt"
    if vault_pass.exists():
        envvars["ANSIBLE_VAULT_PASSWORD_FILE"] = str(vault_pass)

    extravars: dict[str, Any] = {
        "r42_deployment_id": dep.id,
        "r42_attempt_id": attempt.id,
        "r42_scope": attempt.scope,
        "r42_team_id": attempt.team_id,
        "r42_playbook_path": str(playbook_path),
        # team_count is a per-team multiplier the playbook needs on every host
        # (set_fact on localhost is not visible to the proxmox-cli plays).
        "team_count": dep.team_count or 1,
    }

    # Build the tainted-string set for substring redaction. Always includes
    # the vault password and source credentials used for the pinned checkout.
    tainted: set[str] = set()
    if vault_pass.is_file():
        try:
            tainted.add(vault_pass.read_text().strip())
        except OSError:
            pass

    if scenario is not None:
        runtime_vars = target_runtime_variables(target_host, ws)
        extravars.update(runtime_vars)
        tainted.update((target_host.token_ref, runtime_vars["proxmox_api_token_secret"]))
        tainted.add(runtime_vars["default_admin_vm_ci_password"])
        extravars["r42_project_dir"] = str(scenario.project_root)
        extravars["r42_inventory_path"] = str(scenario.inventory)
        envvars["RANGE42_ACTIVE_CONFIG_DIR"] = str(ws)
        # Custom playbooks may use this to keep run output out of the pinned tree.
        extravars["r42_workspace_dir"] = str(ws)
        project = await session.get(Project, dep.project_id)
        source = await session.get(Source, project.source_id)
        if source.token_ref:
            tainted.add(str(source.token_ref))
    else:
        # Legacy path: pre-rendered inventory (e.g. demo_lab) lives under
        # <ws>/inventory/; do not clone or generate anything.
        extravars["r42_inventory_dir"] = str(ws / "inventory")

    # Unlock the workspace's passphrase-protected SSH keys into a dedicated
    # ssh-agent (there is none in the container) so ansible-runner can reach the
    # Proxmox jump + VMs. Returns None when there is no vault/keys, in which case
    # the deploy falls back to the ambient ~/.ssh.
    #
    # Deliberately last: everything above can raise (missing project_sha, git
    # checkout failure, malformed topology, absent Project/Source/ProxmoxHost),
    # and an agent started before those would outlive the failed attempt holding
    # unlocked private keys with nothing to reap it. The only remaining window
    # is runner.start() itself, closed below; after that _run()'s finally owns it.
    ssh_agent = None
    if vault_pass.exists():
        ssh_agent = unlock_workspace_keys(ws, vault_pass)
        if ssh_agent is not None:
            envvars.update(ssh_agent.env)

    runner = runner or DetachedRunner()
    handle = None
    runtime_vault = None
    try:
        # Save the original redaction context before the detached process can
        # emit output. Database credentials can rotate while it is running.
        with os.fdopen(os.open(artifact_dir / "redaction.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as stream:
            json.dump(sorted(value for value in tainted if value), stream)
            stream.flush()
            os.fsync(stream.fileno())
        if scenario is not None:
            runtime_vault = prepare_runtime_vault(ws)
        handle = await runner.start(
            private_data_dir=artifact_dir,
            extravars=extravars,
            envvars=envvars,
        )
        (artifact_dir / "pid").write_text(str(handle.pid or 0))
        running = await mark_attempt_running(
            attempt_id=attempt.id, pid=handle.pid, artifact_dir=artifact_dir,
        )
        await session.refresh(attempt)
        if not running:
            # Cancellation can arrive while Git/runner setup is in progress.
            await handle.kill()
            cleanup_runtime_vault(runtime_vault)
            await finish_attempt(attempt_id=attempt.id, rc=None)
            if ssh_agent is not None:
                ssh_agent.close()
            cleanup_runtime_vault(runtime_vault)
            for path in ("env/envvars", "env/extravars", "command", "redaction.json"):
                shred_envvars(artifact_dir / path)
            return
    except BaseException:
        if handle is not None:
            await handle.kill()
        if ssh_agent is not None:
            ssh_agent.close()
        cleanup_runtime_vault(runtime_vault)
        for path in ("env/envvars", "env/extravars", "command", "redaction.json"):
            shred_envvars(artifact_dir / path)
        raise

    layers = [ConfigDenylistLayer(settings.redaction_denylist),
              VaultTaggedLayer(),
              TaintedStringLayer(tainted_strings=tainted)]
    stop = asyncio.Event()
    watcher = EventsWatcher(
        job_events_dir=artifact_dir / "job_events",
        writer=writer, audit=audit, layers=layers,
        deployment_id=dep.id, attempt_id=attempt.id, stop=stop,
    )
    # The request may expire/close ORM instances immediately after we return.
    # Background work must retain values rather than lazy ORM attributes.
    attempt_id, deployment_id = attempt.id, dep.id

    async def _run() -> None:
        task_watch = asyncio.create_task(watcher.run())
        task_lock = asyncio.create_task(keep_attempt_lock(
            attempt_id=attempt_id, deployment_id=deployment_id, stop=stop,
        ))
        rc = None
        try:
            rc = await handle.wait()
            stop.set()
            await task_watch
            await task_lock
            # Release shared runtime files before the lock permits another run.
            cleanup_runtime_vault(runtime_vault)
            terminal_state = await finish_attempt(attempt_id=attempt_id, rc=rc)
            cursor = writer.append({"event_type": "attempt_end",
                                    "payload": {"terminal_state": terminal_state, "rc": rc}},
                                   attempt_id=attempt_id, deployment_id=deployment_id)
            await finish_attempt(attempt_id=attempt_id, rc=rc, event_cursor_tip=cursor)
            logger.info("attempt finished", attempt_id=attempt_id, rc=rc)
        except asyncio.CancelledError:
            # Graceful shutdown cancels local attempts; hard crashes are
            # recovered using process identity and durable runner artifacts.
            await handle.kill()
            cleanup_runtime_vault(runtime_vault)
            terminal_state = await finish_attempt(attempt_id=attempt_id, rc=rc, cancelled=True)
            try:
                cursor = writer.append({"event_type": "attempt_end", "payload": {
                    "terminal_state": terminal_state, "rc": rc,
                }}, attempt_id=attempt_id, deployment_id=deployment_id)
                await finish_attempt(attempt_id=attempt_id, rc=rc, event_cursor_tip=cursor)
            except OSError:
                pass
            raise
        except Exception as exc:
            await handle.kill()
            cleanup_runtime_vault(runtime_vault)
            await finish_attempt(attempt_id=attempt_id, rc=rc, error_code="ATTEMPT_MONITOR_FAILED")
            logger.warning("attempt monitor failed", attempt_id=attempt_id,
                           exception_type=type(exc).__name__)
        finally:
            stop.set()
            if not task_watch.done():
                task_watch.cancel()
            await asyncio.gather(task_watch, task_lock, return_exceptions=True)
            # Kill the ssh-agent started for this attempt (if any) so it does
            # not outlive the deploy.
            if ssh_agent is not None:
                ssh_agent.close()
            cleanup_runtime_vault(runtime_vault)
            # Shred secrets-bearing files in the runner's private_data_dir.
            # Defense-in-depth: prevents the snapshotted PAT / vault pass from
            # sitting on disk after the attempt completes. Runs on both success
            # and failure paths so cleanup is guaranteed.
            shred_envvars(artifact_dir / "env" / "envvars")
            shred_envvars(artifact_dir / "env" / "extravars")
            shred_envvars(artifact_dir / "command")
            shred_envvars(artifact_dir / "redaction.json")

    task = asyncio.create_task(_run())
    track_attempt(attempt_id, task)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
