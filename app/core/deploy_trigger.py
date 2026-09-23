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
from app.core.attempt_lifecycle import advance_attempt_cursor, finish_attempt, keep_attempt_lock, mark_attempt_running
from app.core.config import settings
from app.core.db import get_session_factory
from app.core.errors import PreflightBlockedError, Range42Error
from app.core.events import EventsWriter
from app.core.events_watcher import EventsWatcher
from app.core.locks import ProvisioningLock, acquire_lock
from app.core.logging import get_logger
from app.core.models import Attempt, Deployment, ProxmoxHost
from app.core.orphans import track_attempt, untrack_attempt
from app.core.preflight import check_vmids
from app.core.scenario import prepare_project_scenario, validate_concrete_scope
from app.core.scenario_networks import check_scenario_networks
from app.core.scenario_resources import check_scenario_resources
from app.core.deployment_allocations import ensure_for_attempt
from app.core.scenario_runtime import cleanup_runtime_vault, prepare_runtime_vault, target_runtime_variables
from app.core.redaction import (
    ConfigDenylistLayer,
    RedactionAuditWriter,
    TaintedStringLayer,
    VaultTaggedLayer,
)
from app.core.runner_detached import DetachedRunner
from app.core.runner_protocol import RunnerProtocol
from app.core.ssh_agent import unlock_workspace_keys, _decrypt_vault
from app.core.attempt_cleanup import cleanup_attempt_credentials, record_attempt_cleanup
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


def resolve_attempt_playbook(scenario_label: str, scope: str) -> Path:
    main = _resolve_playbook_for_scenario(scenario_label)
    if scope == "full":
        return main
    operation = scope
    if scope.startswith("rollback_team_"):
        operation = "rollback_team"
    allowed = {"failed_teams", "team_reset", "teardown", "rollback_all", "rollback_team",
               "rollback_shared", "snapshot_all", "snapshot_team", "snapshot_shared"}
    candidate = main.with_name(f"{operation}.yml") if operation in allowed else None
    if candidate is None or not candidate.is_file() or candidate.resolve().parent != main.parent:
        raise Range42Error(code="OPERATION_UNSUPPORTED", status=409,
                           message=f"Scenario {scenario_label} does not implement {scope}")
    return candidate


async def start_attempt(session: AsyncSession, *, attempt: Attempt,
                        runner: RunnerProtocol | None = None) -> None:
    task = asyncio.current_task()
    attempt_id = attempt.id
    track_attempt(attempt_id, task)
    try:
        dep = await session.get(Deployment, attempt.deployment_id)
        validate_concrete_scope(dep, attempt.scope)
        if dep.project_sha and (dep.native or attempt.scope in ("full", "runtime")):
            root = Path(os.getenv("RANGE42_WORKSPACE_ROOT", str(settings.workspace_root)))
            with ProvisioningLock(root / ".locks") as lock:
                await _start_attempt(session, attempt=attempt, runner=runner, provisioning_fd=lock.fd)
        else:
            await _start_attempt(session, attempt=attempt, runner=runner)
    except Exception as exc:
        await session.rollback()
        await finish_attempt(attempt_id=attempt_id, rc=None,
                             error_code=exc.code if isinstance(exc, Range42Error) else "ATTEMPT_START_FAILED")
        raise
    finally:
        untrack_attempt(attempt_id, task)


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
    runtime_run = None
    native_run = None
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
        if scenario.native:
            from app.core.native_execution import prepare_native_run
            native = scenario.native
            native_run = prepare_native_run(scenario.project_root, native["descriptor"], scenario.context,
                scope=attempt.scope, features=native.get("features", {}), parameters=native.get("parameters", {}),
                artifact_dir=artifact_dir, repository_root=artifact_dir / "checkout")
            playbook_path = scenario.playbook
        elif attempt.scope == "runtime":
            from app.core.runtime_runner import prepare_runtime_run
            runtime_run = await prepare_runtime_run(dep, attempt, target_host, scenario, artifact_dir)
            playbook_path = runtime_run.playbook
        else:
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
        playbook_path = resolve_attempt_playbook(dep.scenario_label, attempt.scope)

    if scenario is not None and not scenario.native:
        # Expiring draft leases cannot authorize execution or release a
        # deployment's assignments. Claim/check before SSH or runner launch.
        await ensure_for_attempt(get_session_factory(), dep, scenario.playbook.parent,
                                 create=attempt.scope == "full", checked_host=target_host)

    writer = EventsWriter(events_jsonl)
    audit = RedactionAuditWriter(redactions_jsonl)
    writer.append({"event_type": "attempt_start",
                   "payload": {"scope": attempt.scope, "team_id": attempt.team_id,
                               **({"operation": attempt.operation} if attempt.operation else {})}},
                  attempt_id=attempt.id, deployment_id=dep.id)

    envvars: dict[str, str] = {
        "RANGE42_TRACE_ID": attempt.id,
        "ANSIBLE_FORKS": "25",
        "ANSIBLE_PIPELINING": "True",
    }
    ca_file = os.getenv("RANGE42_PROXMOX_CA_FILE")
    if ca_file:
        # Ansible URI modules use Python's SSL trust; SDK-based modules use
        # requests. Both must trust the same CA as the verified preflight.
        ca_path = str(Path(ca_file).resolve())
        envvars.update(SSL_CERT_FILE=ca_path, REQUESTS_CA_BUNDLE=ca_path)
    envvars.update(ssh_controlmaster_env(deployment_id=dep.id))
    if provisioning_fd is not None:
        envvars["RANGE42_PROVISIONING_LOCK_FD"] = str(provisioning_fd)
    vault_pass = (scenario.context.workspace if native_run else ws) / "secrets" / "vault_pass.txt"
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

    if not dep.project_sha and attempt.operation and attempt.operation.get("kind") == "legacy":
        extravars.update(attempt.operation.get("variables", {}))

    # Build the tainted-string set for substring redaction. Always includes
    # the vault password and source credentials used for the pinned checkout.
    tainted: set[str] = set()
    if vault_pass.is_file():
        try:
            tainted.add(vault_pass.read_text().strip())
        except OSError:
            pass

    if native_run:
        extravars.pop("r42_playbook_path", None)
        extravars["r42_native_command"] = native_run.command
        # Native Vault/SSH inputs belong to the selected context. Do not replace
        # them with credentials synthesized for generated projects.
        vault_values = await asyncio.to_thread(_decrypt_vault, scenario.context.workspace / "secrets/default_vault.yml", vault_pass)
        def strings(value):
            if isinstance(value, dict):
                for item in value.values():
                    yield from strings(item)
            elif isinstance(value, list):
                for item in value:
                    yield from strings(item)
            elif isinstance(value, str) and len(value) >= 4:
                yield value
        tainted.update(strings(vault_values))
    elif scenario is not None:
        runtime_vars = target_runtime_variables(target_host, ws)
        extravars.update(runtime_vars)
        if attempt.scope == "full":
            from app.core.scenario_preferences import storage_runtime_variables
            extravars.update(storage_runtime_variables(scenario.playbook.parent))
        tainted.update((target_host.token_ref, runtime_vars["proxmox_api_token_secret"]))
        tainted.add(runtime_vars["default_admin_vm_ci_password"])
        extravars["r42_project_dir"] = str(runtime_run.playbook.parent if runtime_run else scenario.project_root)
        extravars["r42_inventory_path"] = str(runtime_run.inventory if runtime_run else scenario.inventory)
        envvars["RANGE42_ACTIVE_CONFIG_DIR"] = str(runtime_run.config_dir if runtime_run else ws)
        # Custom playbooks may use this to keep run output out of the pinned tree.
        extravars["r42_workspace_dir"] = str(ws)
    else:
        # Legacy path: pre-rendered inventory (e.g. demo_lab) lives under
        # <ws>/inventory/; do not clone or generate anything.
        extravars["r42_inventory_dir"] = str(ws / "inventory")
    if scenario and scenario.checkout_credential:
        from urllib.parse import quote
        tainted.update((scenario.checkout_credential, quote(scenario.checkout_credential, safe="")))

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
    if native_run:
        from app.core.ssh_agent import _start_agent
        # range42-context use loads the selected context's keys. Give it an
        # owned agent so it cannot clear the API operator's ambient agent.
        ssh_agent = _start_agent(ws)
        envvars.update(ssh_agent.env)
    elif vault_pass.exists():
        ssh_agent = unlock_workspace_keys(ws, vault_pass)
        if ssh_agent is not None:
            envvars.update(ssh_agent.env)

    runner = runner or DetachedRunner()
    handle = None
    runtime_vault = None

    def cleanup() -> None:
        # The in-memory handles also cover failures before metadata publication.
        if ssh_agent is not None:
            ssh_agent.close()
        cleanup_runtime_vault(runtime_vault)
        cleanup_attempt_credentials(ws, artifact_dir)

    try:
        # Save the original redaction context before the detached process can
        # emit output. Database credentials can rotate while it is running.
        with os.fdopen(os.open(artifact_dir / "redaction.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as stream:
            json.dump(sorted(value for value in tainted if value), stream)
            stream.flush()
            os.fsync(stream.fileno())
        if scenario is not None and not scenario.native:
            runtime_vault = prepare_runtime_vault(ws)
        record_attempt_cleanup(artifact_dir, ssh_agent=ssh_agent, runtime_vault=runtime_vault)
        if runtime_run is not None:
            from app.core.runtime_runner import recheck_runtime_run
            await recheck_runtime_run(dep, attempt, target_host, runtime_run)
        launch = asyncio.create_task(runner.start(
            private_data_dir=artifact_dir,
            extravars=extravars,
            envvars=envvars,
        ))
        try:
            handle = await asyncio.shield(launch)
        except asyncio.CancelledError:
            # Complete process/PID publication if shutdown overlaps spawning.
            # The next API instance can then adopt the independent runner.
            handle = await launch
            raise
        (artifact_dir / "pid").write_text(str(handle.pid or 0))
        running = await mark_attempt_running(
            attempt_id=attempt.id, pid=handle.pid, artifact_dir=artifact_dir,
        )
        await session.refresh(attempt)
        if not running:
            # Cancellation can arrive while Git/runner setup is in progress.
            await handle.kill()
            cleanup()
            await finish_attempt(attempt_id=attempt.id, rc=None)
            return
    except asyncio.CancelledError:
        if handle is None:
            cleanup()
        # A launched runner keeps its credentials and lock through API shutdown.
        raise
    except BaseException:
        if handle is not None:
            await handle.kill()
        cleanup()
        raise

    layers = [ConfigDenylistLayer(settings.redaction_denylist),
              VaultTaggedLayer(),
              TaintedStringLayer(tainted_strings=tainted)]
    stop = asyncio.Event()
    # Background work retains values, not ORM attributes from the request.
    attempt_id, deployment_id = attempt.id, dep.id
    watcher = EventsWatcher(
        job_events_dir=artifact_dir / "job_events",
        writer=writer, audit=audit, layers=layers,
        deployment_id=dep.id, attempt_id=attempt.id, stop=stop,
        on_progress=lambda cursor: advance_attempt_cursor(attempt_id=attempt_id, event_cursor_tip=cursor),
    )
    async def _run() -> None:
        task_watch = asyncio.create_task(watcher.run())
        task_lock = asyncio.create_task(keep_attempt_lock(
            attempt_id=attempt_id, deployment_id=deployment_id, stop=stop,
        ))
        rc = None
        detached = False
        try:
            rc = await handle.wait()
            stop.set()
            stream_error = None
            try:
                await asyncio.shield(task_watch)
            except Exception:
                stream_error = "EVENT_STREAM_FAILED"
            await asyncio.shield(task_lock)
            # Release shared runtime files before the lock permits another run.
            cleanup()
            runtime_result = {}
            if runtime_run is not None:
                from app.core.runtime_completion import observe_runtime_completion
                runtime_result = await observe_runtime_completion(attempt_id, writer)
            terminal_state = await finish_attempt(attempt_id=attempt_id, rc=rc, warning_code=stream_error, **runtime_result)
            cursor = writer.append({"event_type": "attempt_end",
                                    "payload": {"terminal_state": terminal_state, "rc": rc}},
                                   attempt_id=attempt_id, deployment_id=deployment_id)
            await finish_attempt(attempt_id=attempt_id, rc=rc, event_cursor_tip=cursor)
            logger.info("attempt finished", attempt_id=attempt_id, rc=rc)
        except asyncio.CancelledError:
            # Stopping observation is not a user cancellation. The Cancel API
            # signals the runner itself and records cancellation in the DB.
            detached = True
            raise
        except Exception as exc:
            await handle.kill()
            cleanup()
            await finish_attempt(attempt_id=attempt_id, rc=rc, error_code="ATTEMPT_MONITOR_FAILED")
            logger.warning("attempt monitor failed", attempt_id=attempt_id,
                           exception_type=type(exc).__name__)
        finally:
            stop.set()
            # Both workers observe stop. Let any active database transaction
            # and its session close before the API disposes the engine.
            await asyncio.gather(task_watch, task_lock, return_exceptions=True)
            if not detached:
                cleanup()

    task = asyncio.create_task(_run())
    track_attempt(attempt_id, task)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
