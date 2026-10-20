# Durable workshop and deployment write admission

Administrator-only `GET /v1/admin/workshop/admission` reads the installed
controller boundary. `POST` changes it using strict desired input:

```json
{"action":"pause","expected_revision":0,"request_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
```

A resume requires the current revision, exact `pause_id`, another unique request
ID and `confirmation: "RESUME"`. Unknown fields, caller paths, commands, boolean
revisions and unsolicited confirmations are refused. Responses use `no-store`
and a typed projection with no credentials or raw worker errors.

The controller serializes admission and queue claims durably. Its states are
`open`, `draining` and `paused`, with full queued/running counts. Accepted work
drains, emergency `revoke`/`close` remains available, and a later pause cannot be
released with an older proof. Fixed refusal codes are `WORKSHOP_PAUSED`(423),
`WORKSHOP_ADMISSION_CHANGED`(409), and `WORKSHOP_DRAINING`(409).

The deployment Overview shows the control only for its associated legacy cohort.
Old controllers report no supported admission capability; the UI never infers an
effective pause from a local flag. SSH keeps the existing fixed alias/forced-key
scope with explicit strict host-key checking. No controller installation is
performed by this API change.

The scope is **legacy_controller_queue**. Direct deployment attempts, Proxmox
power/configuration routes and external hypervisor writers have independent
paths. A paused idle queue does not reserve RAM, freeze those writers or attest
that an interrupted remote operation ended. Physical rehearsal still requires
their enforcement, exact guest/task readback, persistent node identity and
durable capacity admission. This change does not enable a managed environment's
`execution_ready` flag or qualify infrastructure.

Run the focused route checks with
`python -m pytest -q tests/routes/test_workshop_admission.py tests/routes/test_workshop_management.py`.
The disposable browser test uses actual source API and verified SSH/dispatcher
with synthetic roster/job completion; it is not physical deployment acceptance.


## Managed deployment and VM writes

Existing deployment Overview also offers **Review maintenance access**. This
reads `GET /v1/deployments/{deployment_id}/write-admission`; viewers and operators
can inspect the decision, while only an administrator can change it with `POST`.
The request shape is the same reviewed pause/resume proof above. VM IDs, target,
actor and readiness flags cannot be supplied by the browser. The server obtains
its exact scope from the retained `DeploymentAllocation` and its original target
and pinned revision. It never infers ownership from a name, template or URL.

A committed pause blocks new attempt reservations and checks again immediately
before checkout/runner dispatch. It also blocks snapshot-set execution, allocation
release and raw owned-VM power, deletion, snapshot, configuration, NIC and disk
writes. VM IDs are exclusive across this installation's allocator, so using a
second credential or target alias cannot bypass the pause. Earlier native per-seat
deployment records are checked against their declared VM IDs too; unscoped legacy
deployments require ownership review while a run is closed. Other runs remain
available through their scoped v1 operations. Unscoped legacy v0 administrative
writes are blocked backend-wide while any run is closed. Legacy HTTP writers and
all detached attempt dispatches now share the provisioning lock; an active owned
runner keeps its inherited descriptor even if the API exits.

State is retained in SQLite across restarts. `open`, `draining` and `closed`
describe new dispatch admission and local ownership only. Counts include all
nonterminal attempts, held workspace locks and unresolved native snapshot-set
operations, including a remote operation whose local lock was lost. Accepted
snapshot reconciliation and attempt cancellation remain available; a pause does
not replay, cancel or undo accepted remote work. Exact last-request recovery
returns the original decision, and a stale revision/pause ID cannot resume a
later administrator's pause. Resume requires typed `RESUME` and is refused
while those known operations remain. The UI invalidates previous deployment
preflight approval across pause/resume and discards proofs when the selected
backend, credential or deployment changes.

Missing, altered or corrupt retained ownership fails closed; restore/reconcile
that original scope rather than deleting its pause row. Migration 0014 preserves
existing credentials and refuses downgrade while admission records exist.
`DEPLOYMENT_WRITES_PAUSED` is 423; changed/draining/review-required decisions are 409.

This does **not** freeze the separate legacy participant controller, stop a VM,
attest all previous raw/external PVE workers, reserve physical resources, lend
capacity, or qualify managed environment preparation. `remote_quiescence_verified`
and `capacity_borrowing_allowed` remain false. Do not stop Friday guests or reuse
their RAM based on this decision alone. Operators can run accepted project code;
this is dispatch coordination within one installation, not a sandbox against an
administrator or arbitrary external hypervisor writers. Shared storage writes
outside the owned VM scope require their own future capacity coordination.

Focused checks:
`python -m pytest -q tests/core/test_deployment_admission.py tests/core/test_deployment_admission_migration.py tests/routes/test_deployment_admission.py`.
Source acceptance used a production-built UI, actual API, real Alembic migrations
and synthetic retained allocations. Six browser checks and seven accessibility
scans passed, including actual committed/lost replies, alias refusal, concurrent
administrator proof and mobile keyboard focus. One other synthetic run reserved
an unstarted attempt with auto-start disabled; no runner, provider, VPN or VM
operation ran. All fixture services were stopped after acceptance.
