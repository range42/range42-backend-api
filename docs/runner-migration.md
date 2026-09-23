# Runner migration: detached execution

The v1 runtime launches `ansible-runner run` in an independent process session.
The private directory is `<workspace>/runner/<attempt-id>`; `--artifact-dir`
points to its parent and `--ident` is the attempt ID. Return codes, process
identity, and `job_events` therefore remain under that attempt's directory.
The backend observes the process to completion and writes redacted events to
`<workspace>/events.jsonl`.

See [runner lifecycle persistence](runner-lifecycle.md) for lock ownership,
credential cleanup, cancellation, and restart recovery. Saved authored
scenarios use the same lifecycle through [existing Range42 contexts](native-scenarios.md).

Legacy installed scenarios retain their explicit operation playbooks:
`team_reset.yml`, `failed_teams.yml`, `teardown.yml`, and the scoped
`snapshot_{all,team,shared}.yml` / `rollback_{all,team,shared}.yml` files.
Unsupported actions return `409 OPERATION_UNSUPPORTED`; an action never falls
back to the full provisioning playbook. Server routes provide the requested
team ID, snapshot name, or validated snapshot records as runner variables.
Snapshot, rollback, reset, and teardown use the same attempt reservation as full
provisioning, so they cannot replace active work. Teardown preserves inventory,
credentials, and audit logs. `_universal` remains retired.
