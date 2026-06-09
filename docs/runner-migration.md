# Runner migration: in-process to detached CLI subprocess

v0 used the in-process `ansible_runner.interface.run()` (blocking) and
`ansible_runner.interface.start()` (background thread in the current
process). The latter dies with FastAPI. v1 spawns:

```
ansible-runner start <private_data_dir>
```

via `asyncio.create_subprocess_exec(..., start_new_session=True)`. The
subprocess daemonises itself, writes `pid`, `status`, `rc` under
`<private_data_dir>/`, and streams JSON event files to
`<private_data_dir>/job_events/`. FastAPI reads from `events.jsonl`
(written by the EventsWatcher); it never reads ansible-runner's files
directly from the route layer.

On FastAPI restart, `app/core/orphans.py:scan_workspaces()` walks
`~/range42.config/*/runner/pid` and classifies each:

- Alive (`kill -0` succeeds): re-adopt as observer; spawn a new
  EventsWatcher against the existing artifact dir.
- Dead + last events.jsonl line older than 60s: mark attempt
  state=`unknown` per spec section 12. Never silently fail -- `unknown`
  blocks teardown until human-resolved.
- Dead + fresh last event: `completed_unflushed` -- flush and reclassify.
