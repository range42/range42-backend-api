# Persistent deployment assignments

Pinned concrete projects can transfer a reviewed draft reservation into a
nonexpiring deployment allocation. The allocation protects literal VM IDs and
bridge/IPv4 address pairs even after a failed attempt, database restart, stopped
guest, or the original draft expiry. It does not authorize reuse of an existing
guest during a full retry.

Run `alembic upgrade head` before starting the paired backend/UI release.
`0006_deployment_allocations` adds the deployment ledger and records the API URL
on draft leases. Existing leases survive migration, but must be renewed and
reviewed before transfer because their original target URL was not recorded.
Existing deployment rows are not retrospectively asserted to own resources.

## Draft handoff

`POST /v1/deployments/` accepts optional `allocation_reservation_id` alongside
the normal deployment fields and pinned `project_sha`. Send the original private
owner token in `X-Range42-Reservation-Token`, in addition to API bearer auth.
The browser's local project identity is separate from the registered API project
ID. The paired UI validates local ownership, backend, selected target, expiry
and pin before submission, then re-reads the private proof at the send boundary.

The backend checks out the pinned project into an isolated temporary directory
and compares its complete literal VM ID/NIC index/bridge/address mapping to the
lease. Git I/O completes before taking the database writer lock. Deployment
creation, durable allocation creation, and lease consumption commit in one
transaction, coordinated with draft allocation writers. Wrong ownership, expiry,
changed targets, mismatched mappings and failed commits retain the original
lease and leave no deployment allocation behind.

The durable record preserves normalized VM names and NIC identities, the saved
revision, target API URL/node and original source assignment keys when supplied
by a lease. Neither the private token nor its digest is copied into that record.
The UI leaves its local metadata intact on success/error; a consumed lease cannot
be reused to create another deployment. After an ambiguous response, inspect the
deployment list before retrying, rather than clearing ownership or silently
submitting without the lease.

## Launch and release

A first full attempt on a pinned project without a transferred lease claims its
manifest assignments before SSH or runner launch. It cannot take resources held
by another active draft or committed deployment. Subsequent full/configure/
runtime/teardown attempts verify an existing record against the target and
manifest; configure content revisions still preserve the original deployment pin
and topology. Full-retry guest existence/ownership checks remain in force. Older
pinned v1/v2 manifests can record only the literal IDs/addresses they declare;
empty-VM content scenarios need no allocation record.

`GET /v1/deployments/{id}/allocations` returns the committed assignments, original
project revision, host/node and creation time. A missing record returns 404.
There is no expiry or automatic release on process exit, cancellation or teardown
success: an arbitrary playbook exiting successfully is not proof of deletion.

`DELETE /v1/deployments/{id}/allocations` explicitly releases the record. It
requires unchanged target binding, no active attempt or held workspace lock, no
unknown deployment state, and successful global Proxmox `nextid` assertions for
**every** claimed VM ID, including IDs hidden from filtered resource lists.
The provisioning flock is held through these checks. HTTP reads occur outside
the SQLite writer transaction; the record, target and current attempt are checked
again under writer serialization before deletion. Timeout, permission failure,
remaining VM, changed attempt or target retains all assignments. This endpoint
does not delete guests, networks, files, history or snapshots. There is not yet a
dedicated release control in the UI; operators can use the authenticated endpoint.

## Limits

This is installation-wide coordination under the shared API operator identity,
not per-user authorization or a lock on external Proxmox writers. Claims remain
conservative across registered host aliases and separate clusters. Release proves
guest absence at the time of the reads; independent infrastructure writers still
need external coordination. Draft allocation retains its existing live-address
and installed-ledger audits. Manual execution retains its existing preflight
coverage; the new claim transaction is not an additional IP discovery audit.

Unpinned installed-scenario execution remains outside committed-ownership
coverage; its installed scenario ledger is still excluded by draft allocation.
There is no automatic backfill for historical deployments, no contiguous VMID
block/subnet/VNet ownership, additive growth, automatic partial retry or general
IPAM. Those parts of API issue107 remain unfinished. Releasing claims does not
make guest-static/DHCP/external addresses observable.

Regression coverage uses real pinned Git checkout and SQLite, independent
concurrent requests, failed commits, process-free fake PVE occupancy, a release
check racing a new attempt, and existing real local Ansible execution/configure
tests. The existing single-guest and shared browser release evidence predates
this change; this document does not claim a new shared deployment acceptance.
