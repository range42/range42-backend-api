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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select, update, case
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.allocation import ssh_controlmaster_env
from app.core.config import settings
from app.core.errors import ProjectCheckoutError, Range42Error
from app.core.events import EventsWriter
from app.core.events_watcher import EventsWatcher
from app.core.inventory_writer import write_inventory
from app.core.locks import acquire_lock, heartbeat, release_lock
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
from app.core.ssh_agent import unlock_workspace_keys
from app.core.workspace import shred_envvars
from app.utils.checks_playbooks import resolve_scenarios_playbook

logger = get_logger(__name__)

_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()
HEARTBEAT_INTERVAL_S = 30


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
                        runner: RunnerProtocol | None = None,
                        lock_acquired: bool = False,
                        operation_vars: dict[str, Any] | None = None) -> None:
    """Own the workspace until actual execution ends, including setup errors."""
    owner = f"attempt-{attempt.id}"
    if not lock_acquired:
        await acquire_lock(session, deployment_id=attempt.deployment_id,
                           owner=owner, interval_s=30)
    dep = await session.get(Deployment, attempt.deployment_id)
    attempt.state = "deploying"
    attempt.started_at = datetime.now(timezone.utc)
    dep.current_attempt_id = attempt.id
    dep.state = "deploying"
    await session.commit()
    dep_id, att_id = dep.id, attempt.id
    try:
        await _launch_attempt(session, attempt=attempt, runner=runner,
                              operation_vars=operation_vars)
    except BaseException:
        await session.rollback()
        attempt = await session.get(Attempt, att_id)
        dep = await session.get(Deployment, dep_id)
        attempt.state = "failed"
        attempt.ended_at = datetime.now(timezone.utc)
        attempt.sub_reason = "RUNNER_SETUP_FAILED"
        dep.state = "failed"
        await release_lock(session, deployment_id=dep.id, owner=owner)
        await session.commit()
        private = Path(dep.workspace_path) / "runner" / attempt.id
        for name in ("envvars", "extravars"):
            shred_envvars(private / "env" / name)
        raise


async def _launch_attempt(session: AsyncSession, *, attempt: Attempt,
                          runner: RunnerProtocol | None = None,
                          operation_vars: dict[str, Any] | None = None) -> None:
    """Spawn the detached runner for an attempt and begin event watching.

    The caller owns the workspace lock. Prepare inputs, start the runner,
    and observe it using independent database sessions until completion.
    """
    dep = (await session.execute(
        select(Deployment).where(Deployment.id == attempt.deployment_id))
    ).scalar_one()
    ws = Path(dep.workspace_path)
    events_jsonl = ws / "events.jsonl"
    redactions_jsonl = ws / "redactions.jsonl"
    artifact_dir = ws / "runner" / attempt.id
    artifact_dir.mkdir(parents=True, exist_ok=True)

    playbook_path = resolve_attempt_playbook(dep.scenario_label, attempt.scope)

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
        # team_count is a per-team multiplier the playbook needs on every host
        # (set_fact on localhost is not visible to the proxmox-cli plays).
        "team_count": dep.team_count or 1,
    }

    extravars.update(operation_vars or {})

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

        # Proxmox API creds for the _universal playbook's node-network tasks
        # (the proxmox_controller role reads proxmox_api_* as plain vars; the
        # generated inventory carries no token). token_ref: "user!tokenid=secret".
        extravars["proxmox_api_host"] = api_url.split("://", 1)[-1].rstrip("/")
        extravars["proxmox_node"] = target_host.node_name
        _tok = target_host.token_ref or ""
        if "!" in _tok and "=" in _tok:
            _userpart, _secret = _tok.split("=", 1)
            _user, _tokid = _userpart.split("!", 1)
            extravars["proxmox_api_user"] = _user
            extravars["proxmox_api_token_id"] = _tokid
            extravars["proxmox_api_token_secret"] = _secret
            tainted.add(_secret)

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
    try:
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
            job_events_dir=Path(getattr(handle, "artifact_dir", artifact_dir / "artifacts" / "execution")) / "job_events",
            writer=writer, audit=audit, layers=layers,
            deployment_id=dep.id, attempt_id=attempt.id, stop=stop,
        )

        # Background work must never reuse the request's session.
        factory = async_sessionmaker(session.bind, expire_on_commit=False)
        dep_id, att_id = dep.id, attempt.id
        owner = f"attempt-{att_id}"
        attempt.pid = handle.pid
        attempt.artifact_dir = str(getattr(handle, "artifact_dir", artifact_dir))
        await session.commit()

    except BaseException:
        # No observer owns this subprocess yet. Stop it before the caller
        # marks setup failed and releases the workspace for another attempt.
        if handle is not None:
            await handle.kill()
        if ssh_agent is not None:
            ssh_agent.close()
        raise

    async def _renew() -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)
            async with factory() as db:
                if not await heartbeat(db, deployment_id=dep_id, owner=owner):
                    return
                await db.commit()

    async def _run() -> None:
        task_watch = asyncio.create_task(watcher.run())
        task_heartbeat = asyncio.create_task(_renew())
        completed = False
        try:
            rc = await handle.wait()
            completed = True
            stop.set()
            stream_failed = False
            try:
                await task_watch
            except Exception:
                stream_failed = True
                logger.exception("attempt event collection failed", attempt_id=att_id)
            async with factory() as db:
                terminal = (await db.execute(update(Attempt).where(Attempt.id == att_id).values(
                    state=case((Attempt.sub_reason == "cancel_requested", "cancelled"),
                               else_="succeeded" if rc == 0 else "failed"),
                    rc=rc, ended_at=datetime.now(timezone.utc),
                    sub_reason="EVENT_STREAM_FAILED" if stream_failed else None,
                ).returning(Attempt.state))).scalar_one()
                await db.execute(update(Deployment).where(
                    Deployment.id == dep_id, Deployment.current_attempt_id == att_id,
                ).values(state=terminal))
                # Write the last event while still owning the workspace. A new
                # writer must recover its cursor after this append.
                try:
                    writer.append({"event_type": "attempt_end",
                                   "payload": {"terminal_state": terminal, "rc": rc}},
                                  attempt_id=att_id, deployment_id=dep_id)
                except OSError:
                    logger.exception("attempt terminal event write failed", attempt_id=att_id)
                    await db.execute(update(Attempt).where(Attempt.id == att_id).values(
                        sub_reason="EVENT_STREAM_FAILED"))
                await release_lock(db, deployment_id=dep_id, owner=owner)
                await db.commit()
            logger.info("attempt finished", attempt_id=att_id, rc=rc)
        finally:
            stop.set()
            task_heartbeat.cancel()
            await asyncio.gather(task_heartbeat, return_exceptions=True)
            await asyncio.gather(task_watch, return_exceptions=True)
            # On web-worker shutdown the separate runner session remains alive.
            # Keep its agent and inputs until an observer sees actual completion.
            if completed:
                if ssh_agent is not None:
                    ssh_agent.close()
                shred_envvars(artifact_dir / "env" / "envvars")
                shred_envvars(artifact_dir / "env" / "extravars")

    task = asyncio.create_task(_run())
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
