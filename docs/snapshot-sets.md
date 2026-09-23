# Reviewed concrete snapshot sets

Snapshot sets cover 1–64 QEMU guests from the deployment's pinned concrete VM manifest. They use the registered Proxmox host, existing protected-VM checks, an exact deployment description marker, VM name and hardware UUID. LXC, memory snapshots and unpinned installed scenarios are outside this interface. The API uses the existing backend authentication policy and registered Proxmox privileges; a plan digest is an optimistic review token, not separate authorization.

The set is **not atomic**. Each guest has its own native task and outcome. A running-guest disk snapshot is not a guarantee of application consistency. No call restarts a guest after rollback; the initial contract requires stopped-state readback after the disk-only rollback. Review application shutdown/quiescing separately.

All paths below start with `/v1/deployments/{deployment_id}/snapshot-sets`:

| Request | Result |
| --- | --- |
| `GET ?offset=0&limit=20` | Bounded metadata list, no Proxmox writes. |
| `POST /plan` with `{name?, description?, vmstate:false}` | Read owned guests and persist a five-minute create plan; return 201. |
| `GET /{set_id}` | Set, latest operation, reviewed members and durable per-member outcomes. |
| `POST /{set_id}/execute` with `{plan_digest}` | Revalidate exact target and members, reserve one durable attempt and submit each native task once; return 202. |
| `POST /{set_id}/reconcile` | Read saved task IDs and native snapshot/configuration state; never dispatch or repeat writes. |
| `POST /{set_id}/rollback/plan` | Review a completed set and its original native snapshot proofs. |
| `POST /{set_id}/delete/plan` | Review existing owned snapshots in a terminal set; unrelated snapshots are untouched. |
| `DELETE /{set_id}/plans/{operation_id}` | Cancel only an unexecuted plan's metadata; return 204. |
| `POST /retention/plan?limit=20` | Review deletion candidates under the current retention policy; return individual executable plans, without deleting anything. |

`name` is a human label (1–128 characters); `description` is at most 1,024 characters. The server chooses the native snapshot name and exact set ownership marker. Request `vmstate:true` is rejected. Plans bind the deployment revision, registered host/URL/node/credential/protection digest, guest UUID/name/configuration/status, and native snapshot proofs where applicable. Re-registration, expiry, pending configuration, an ownership change or a reused plan refuses dispatch. All members are checked before the first write, then each is checked again before its own write. The native API does not provide a transaction spanning those reads and writes: external Proxmox administrators must coordinate mutations.

An accepted operation has a durable `snapshot_set` Attempt, workspace lock and per-member write intent recorded **before** HTTP dispatch. The response may already be `needs_review` if a write response was lost. Accepted does not mean successful. Member states include `accepted`, `succeeded`, `failed`, `not_started` and `unconfirmed`; terminal aggregate outcomes distinguish success, partial success and failure. No failure triggers automatic rollback, deletion or a second dispatch.

The normal orphan observer reconciles known saved UPIDs after restart. `operation.recovery` is `poll_saved_tasks`, `operator_required`, or `none`. If dispatch may have occurred but its UPID was lost, this version cannot safely adopt an arbitrary task or prove remote inactivity: **operator recovery is unavailable through this API**. It retains the active attempt and workspace lock. Cancelling the attempt, expiring the heartbeat or pressing Reconcile never releases that ambiguous ownership. Preserve the database and obtain independently reviewed native task/guest evidence before an offline recovery; do not retry the original write.

The shared provisioning lock covers finite dispatch and raw per-VM mutation admission. Active set members also block native per-VM snapshot/power/delete and configuration writes, including aliases using the same VMID. Reads remain available. Already accepted raw native tasks, v0 clients and external Proxmox writers are not retroactively journaled by this interface; coordinate them before review. A host binding change prevents even task polling against the replacement target.

Successful create readback checks the owned native snapshot, creation timestamp and normalized snapshot configuration. Rollback checks that the saved snapshot proof is unchanged, the original guest UUID remains, configuration matches the saved snapshot and the VM is stopped. These checks do not inspect restored guest files or claim whole-application recovery. Snapshot configuration normalization follows Proxmox's handling of description, unused volumes, generation and runtime metadata. See the primary [snapshot implementation](https://github.com/proxmox/pve-guest-common/blob/master/src/PVE/AbstractConfig.pm) and [QEMU API contract](https://github.com/proxmox/qemu-server/blob/master/src/PVE/API2/Qemu.pm).

## Retention is reviewed deletion

`GET` and `PUT /v1/admin/retention` return `keep_count`, `keep_days`, `automatic_enforcement:false` and `execution:"reviewed_snapshot_sets_only"`. PUT accepts only the two integer preferences. There is no automatic expiry worker.

Candidates are completed owned sets within one deployment. Keep the newest `keep_count` **or** sets younger than `keep_days`; both protections apply. Partial, failed, active and foreign/native-only snapshots are never retention candidates. The plan reads at most 1,000 completed sets and returns at most 20 candidates. A corrupt policy blocks deletion review. Each returned candidate still needs an explicit execution of its reviewed digest. Policy or retention eligibility changes before execution require a new review. Already completed deletions are never undone automatically.

## Persistence and rollout

Migration `0007_snapshot_sets` adds `snapshot_sets` and `snapshot_operations` without rewriting deployments, leases or native resources. A populated snapshot journal blocks downgrade because discarding it could hide remote work or destroy recovery evidence. Back up and preserve the database before an independently reviewed offline downgrade. Source tests use local Git, SQLite and a simulated native API; no live snapshot/rollback/delete acceptance or storage-specific capability is claimed by those tests.
