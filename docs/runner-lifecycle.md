# Runner lifecycle persistence

`app/core/attempt_lifecycle.py` persists the process lifecycle using fresh DB
sessions, so a background runner does not retain the HTTP request's session.

- After spawn, `mark_attempt_running` records the PID, artifact directory and
  start time. Both the attempt and its current deployment become `deploying`.
  A false return means the attempt is missing, already terminal, or has a cancellation request; the caller
  must stop the spawned process.
- On exit, `finish_attempt` records the real return code and end time. A zero
  return code becomes `succeeded`; a nonzero return code becomes `failed`.
  Monitoring errors use a stable error code with no invented return code.
  If execution finishes but event observation fails, the real process result
  remains authoritative and `EVENT_STREAM_FAILED` records the missing stream.
- Explicit cancellation and already persisted terminal results survive late
  process callbacks. Only the current attempt updates the deployment, and only
  the matching attempt owner can release its workspace lock.
- Call `finish_attempt` before writing `attempt_end`, and use its returned state
  in that event. It can be called again with the final event cursor; repeated
  calls preserve the result and timestamp and never move the cursor backwards.
- Run `keep_attempt_lock` alongside the runner and await it during cleanup. It
  renews the owned lock using short independent sessions until its stop event
  is set or ownership is lost. Database failures propagate to the caller.

The attempts endpoint reserves the current attempt atomically. A second request
while that attempt is active returns `409 ATTEMPT_IN_PROGRESS`. Setup failures
persist a failed attempt and release its lock even when the event file cannot
be written. A live workspace lock also blocks replacement during cancellation
shutdown. Expired locks are cleaned only for the requested deployment; an
in-flight attempt still blocks reservation even when its lock is expired.

Concrete attempts select an explicit `main.yml`, `configure.yml` or `teardown.yml`.
Their effective commit is stored in `Attempt.project_sha`; a configure-only
revision override must preserve the original inventory and VM/network manifests.
The deployment's original commit remains unchanged. Teardown uses the same
reservation and process lifecycle, requires codename confirmation, and retains
the workspace and history.

Target credentials are injected as runtime extra variables and redacted from
events. A missing bundle vault gets a private empty placeholder; cleanup removes
only the unchanged file created by this attempt, before releasing the workspace
lock. Existing or replaced user vaults are preserved.

## Restart recovery and infrastructure serialization

Graceful API shutdown stops local observation while retaining the independent
runner, its credentials and workspace ownership. Observers signal their
heartbeat and event workers to stop, then await
in-flight database operations and session closure before engine disposal. This
also applies if shutdown arrives while terminal events are being drained.
The Cancel API separately
signals only the current attempt's verified process and records `cancel_requested`.
The attempt remains active until the process exits, when completion records
`cancelled` and releases its lock. Cancellation during tracked setup prevents a
late spawn from becoming active. If no process or setup task can be found, the
endpoint returns `409 RUNNER_NOT_RUNNING` instead of claiming cancellation.
A shutdown overlapping process
launch waits for its PID publication, so recovery can find it even before its
database running-state update. After a restart, `orphans.py` reads database attempts and
their `runner/<attempt-id>` artifacts. It adopts only a process whose persisted
PID, Linux start time and boot ID still match. It restores workspace heartbeats
and drains new events through the original redaction context. Persisted source
event IDs prevent replay duplicates. An observed runner `rc` restores success
or failure; a missing exit result becomes `unknown`. Interrupted preparation
without a PID becomes unknown after a 90-second grace period. Cancellation
remains authoritative when recovered runner results arrive later.

New attempts store a private redaction context before spawning and remove it
with runner credential files at terminal cleanup. Old artifacts without that
context are not replayed as raw output. A process without verifiable identity
is never adopted or signalled; operators must inspect an unknown outcome
before retrying infrastructure changes.

Before launch, private `cleanup.json` records the SSH-agent PID/start time/boot
identity, its workspace socket directory and device/inode identities, and the
device/inode of a backend-created empty runtime vault. Recovery
terminates only that original agent and removes only its original socket and
unchanged vault, then
removes runner credentials before releasing the workspace lock. Existing user
vaults and replacement files are preserved. The agent is signalled through a
Linux process descriptor after checking identity; unsupported kernels or denied
process access skip termination rather than signal an unverified PID. This uses
[Python's Linux pidfd API](https://docs.python.org/3/library/os.html#os.pidfd_open)
and [descriptor-based signals](https://docs.python.org/3/library/signal.html#signal.pidfd_send_signal).
Older attempts without cleanup metadata cannot safely reclaim an unrecorded
SSH agent or claim ownership of an existing vault.

Agents use an explicit socket inside a new mode-0700 `.agent-*` workspace
directory. This survives an API service restart with `PrivateTmp=true`:
systemd deletes service-private `/tmp` contents on stop even when processes
survive. See [systemd's PrivateTmp documentation](https://raw.githubusercontent.com/systemd/systemd/main/man/systemd.exec.xml).
Terminal cleanup checks the recorded directory and socket inodes and never
recursively deletes socket directories. Owned agents receive an identity-checked
SIGKILL because their SIGTERM handler would blindly unlink a replacement at the
original path. Unknown files, symlinks and replaced directories are preserved.
The full socket pathname must fit Linux's 107-byte limit; a longer workspace
root fails before starting an agent instead of falling back to temporary storage.
The shared installation's Ansible local temp and SSH ControlMaster directories
are under the service account's home, outside systemd's private `/tmp`.

The service manager must preserve child processes on API restart (the shared
deployment uses systemd `KillMode=process`). A container or VM shutdown still
stops its processes; persisted exit evidence then determines the recovered
result. Release tooling must refuse to replace runtime files while any runner
is active. Credential files remain private plaintext for the running Ansible
process and are not described as encrypted execution storage.

After runner exit, terminal cleanup removes raw `job_events/*.json` only when
this attempt has corresponding source-event receipts in canonical `events.jsonl`.
It fsyncs that canonical log before deleting its raw inputs. The redundant raw
`stdout` file is removed only after all job-event entries have been processed.
Canonical events, redaction audit, return code, status and process identity are
retained. Linked paths and still-running verified processes are not cleaned.
Unprocessed raw files/stdout remain private for operator review, with a warning
containing counts only. Runner `stderr` is retained separately and may contain
raw diagnostics; this does not claim that every execution artifact is sanitized.
Unlinking files does not erase older filesystem snapshots or backups.

Full provisioning is serialized across this installation by a filesystem lock.
The detached runner inherits its descriptor, so an API crash does not allow
another full run to race the first runner's SDN writes and apply. The lock covers
all target aliases. It coordinates this backend's runners; administrators and
other applications writing directly to Proxmox must coordinate separately.
Keep one API worker and the database/workspace on local persistent storage.

`_universal` execution has been removed. New requests reject that label and old
deployments produce `SCENARIO_RETIRED`; save a concrete scenario and create a
new deployment from its commit. Existing history remains readable.
