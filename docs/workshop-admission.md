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

## Importing original native ownership

The existing fixed workshop predates `DeploymentAllocation`. An administrator can
review and import its original generations from **Deployment Overview → Existing
workshop ownership**. This is a compatibility handoff for existing resources;
new catalog runs continue to use the normal allocator and native plan.

Pause participant management and drain accepted queue work first. The backend
then obtains original receipt, VM generation, source commit, target and network
evidence through the restricted controller. Verified HTTPS independently checks
the actual API certificate, generated node certificate and both current and
configured guest attachments. Names or current VM tags alone cannot establish
ownership. The browser supplies neither VM IDs, generation records nor paths.
The public import schema accepts bounded native assignments; the controller's
private adapter owns the installed workshop's fixed scope and receipt policy.

Administrator-only endpoints, all with `Cache-Control: no-store`:

- `GET /v1/deployments/{id}/allocations/import`: retained local handoff status.
- `POST /v1/deployments/{id}/allocations/import/review`, body `{}`: fresh review.
- `POST /v1/deployments/{id}/allocations/import`, body
  `{"review_revision":"<server review revision>"}`: save or explicitly finish
  that same original handoff.

SQLite retains an intent before the controller saves its protected pending
record. The backend then commits one canonical allocation before the controller
acknowledges its exact identity through a read-only callback. No SQL writer is
held across that callback. Both sides retain the original import and allocation
IDs. Missing or partial original records require recovery; they never cause a
fallback to name-based ownership. A lost reply leaves `pending_controller` or
`claim_saved` visible. Overview reads that status without replaying the write;
**Finish ownership handoff** deliberately resumes the same retained intent.
Neither side resumes its queue automatically. The controller's ownership RPCs
have a total 110-second deadline, below the trusted transport's 120-second limit.

After acknowledgement, legacy seat work runs on the canonical parent through
`POST /v1/deployments/{id}/workshop/legacy/participants/{username}/preflight`
and `/actions`. Preflight accepts only `action`; actions also require a durable
`dispatch_id` and exact `confirm_username` for reset or teardown. The backend
derives the seat and pinned native parameter override from imported ownership,
without changing the parent record. A dispatch journal and its attempt commit in
one serialized transaction: repeating a queue job returns its original attempt,
including failed, cancelled or uncertain results, without restarting it. The
controller stops creating overlapping per-seat deployments. Old unscoped fixed
workshop attempts are refused, and native dispatch rejects VM IDs owned by a
different canonical deployment. New run authoring retains its original allocator.

Verified canonical allocation release leaves a `released` tombstone. The old
controller cannot use it to recreate retired guests. Migration 0015 refuses to
erase retained handoffs or dispatch decisions on downgrade. Preserve original
controller state, canonical claims, receipt files and the database together for
recovery; dropping both sides is not a supported reset procedure.

An ownership import does not freeze other writers, prove remote PVE task
completion, grant a physical capacity reservation, prepare a new environment or
lend Friday's RAM. `remote_quiescence_verified` and
`capacity_borrowing_allowed` remain false. Legacy controller admission and
managed VM write admission remain separate, explicit decisions.

## Read configured physical resources

An administrator with a current retained physical-node binding can request
`POST /v1/proxmox/hosts/{host_id}/physical-node/capacity/review` with the closed
body `{}`. The dedicated root inspector must separately enable the fixed
capacity read capability. A browser cannot supply limits, commands, a node,
backing identities or readiness flags. Responses are `no-store`.

The API checks the original node/hardware, installation and root policy against
the retained binding, independently verifies the actual HTTPS leaf and generated
node certificate, and rechecks target, credential and agent configuration after
the observation. Changed or missing binding facts require a fresh identity
review. Aliases remain one physical node.

The response includes bounded current/pending guest commitments, the explicit
host memory reserve/shared CPU budget and conservative disk facts. Stopped
guests keep their resumable resources. Negative memory headroom remains negative
in the response and UI. The read creates no allocation, attempt or reservation;
`allocation_granted`, `execution_ready` and `infrastructure_qualified` are false.
The UI exposes this under existing environment Settings, after identity review.
Refresh or changed target/credentials discards old evidence.

This is an observation contract. Durable physical holds, shared pool/cluster
accounting across concurrent runs, original-generation reconciliation and scoped
maintenance/resume guards are still required before capacity admission or loans.
A current free-memory sample or a successful read cannot authorize VM suspension
or a new workshop deployment.
