# Runner execution and lifecycle

v1 invokes the pinned `ansible-runner` CLI as:

```
ansible-runner run <workspace>/runner/<attempt_id> --ident execution --playbook scenarios/<scenario>/main.yml
```

The subprocess has its own session (`start_new_session=True`). Using `run`
keeps the spawned PID and exit status attached to the actual execution;
`start` double-forks and its launcher exiting does not mean the playbook ended.
The `--playbook` option belongs on the runner command, not in `env/cmdline`.
`env/envvars` and `env/extravars` are JSON mappings with mode `0600`.

The API writes the PID to `runner/<attempt_id>/pid` and stores the PID and
artifact directory on the attempt. Runner writes `rc`, `status`, and
`job_events/` under `runner/<attempt_id>/artifacts/execution/`.
The event observer drains remaining events after exit, persists the result,
and emits the terminal event before releasing the workspace lock. Success
uses `succeeded`; nonzero exit uses `failed`. Setup failures are also persisted.

Lock acquisition is atomic. A second submission returns `409 DEPLOYMENT_LOCKED`
without replacing the active attempt. The observer renews the lease every
30 seconds and releases it after execution. The SSH agent and runner inputs
are retained during execution and cleaned up after exit.

Cancellation targets the current attempt's PID only. A missing process returns
`409 RUNNER_NOT_RUNNING`. An accepted signal records `cancel_requested` in
`sub_reason`; the attempt remains active until the observer confirms exit and
persists `cancelled`. A completion racing cancellation cannot be overwritten.

## Scoped operations

Full deployment uses `main.yml`. Other operations require an explicit file
beside it:

| Operation | Playbook |
| --- | --- |
| Retry failed teams | `failed_teams.yml` |
| Reset a team | `team_reset.yml` |
| Teardown | `teardown.yml` |
| Snapshot | `snapshot_all.yml`, `snapshot_team.yml`, `snapshot_shared.yml` |
| Rollback | `rollback_all.yml`, `rollback_team.yml`, `rollback_shared.yml` |

Absent implementations return `409 OPERATION_UNSUPPORTED` before creating an
attempt. They never fall back to `main.yml`. Existing scenario repositories
must provide these operation playbooks to enable the corresponding endpoints.

The runner receives `r42_scope`, `r42_team_id`, and the existing deployment
variables. Snapshot requests also pass `r42_snapshot_name`; rollback passes
`r42_snapshot_id` and the validated `r42_snapshots` list (`vm_id`, `name`).
Teardown and rollback must use their dedicated endpoints so confirmation and
snapshot checks cannot be bypassed through generic attempt creation.

Teardown retains the workspace, including inventory, credentials, and audit
logs. Filesystem retention is separate from infrastructure operations.

## Process restarts

A web-worker exit does not itself terminate the runner session. The legacy
orphan reconciler only classifies/logs processes; automatic observer reattachment
is not implemented. Operators must reconcile interrupted observation before
starting further work on that deployment. Restart recovery is separate from the
normal execution lifecycle described above.
