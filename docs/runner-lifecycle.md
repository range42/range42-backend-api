# Runner lifecycle persistence

`app/core/attempt_lifecycle.py` persists the process lifecycle using fresh DB
sessions, so a background runner does not retain the HTTP request's session.

- After spawn, `mark_attempt_running` records the PID, artifact directory and
  start time. Both the attempt and its current deployment become `deploying`.
  A false return means the attempt is missing or already terminal; the caller
  must stop the spawned process.
- On exit, `finish_attempt` records the real return code and end time. A zero
  return code becomes `succeeded`; a nonzero return code becomes `failed`.
  Monitoring errors use a stable error code with no invented return code.
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

Graceful cancellation of a local background monitor stops the process and
persists `cancelled`. After a hard crash, `orphans.py` reads database attempts and
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

Full provisioning is serialized across this installation by a filesystem lock.
The detached runner inherits its descriptor, so an API crash does not allow
another full run to race the first runner's SDN writes and apply. The lock covers
all target aliases. It coordinates this backend's runners; administrators and
other applications writing directly to Proxmox must coordinate separately.
Keep one API worker and the database/workspace on local persistent storage.

`_universal` execution has been removed. New requests reject that label and old
deployments produce `SCENARIO_RETIRED`; save a concrete scenario and create a
new deployment from its commit. Existing history remains readable.
