# Host capacity and deployment estimates

`GET /v1/proxmox/hosts/{host_id}/capacity` is an authenticated, read-only API for
an onboarded target. It returns an observation timestamp, `available`, `partial`
or `unavailable` status, and these measurements:

- `cpu.logical_cpus` and `cpu.utilization` (a fraction from 0 to 1).
- `memory.total_bytes`, `used_bytes` and `free_bytes`.
- `storage[]`: pool name/type/content, enabled/active/shared state and the same
  byte fields. Each pool is reported separately; their totals are not summed.
- `issues[]` with stable codes, affected resource and an actionable message.
- `limitations[]` describing scheduling, permission visibility and storage
  allocation limits.

Unknown values are `null`. Zero free RAM or disk remains a measured zero.
Malformed, negative and inconsistent values are rejected. An inactive or
disabled pool has no usable `free_bytes` measurement. A node failure can still
return readable storage measurements, and a storage failure preserves node
measurements. Upstream errors omit credentials and response bodies.

Proxmox node status requires `Sys.Audit` on the selected node. Its CPU count is
logical hardware capacity and its utilization is a current sample; neither
represents exclusive free cores. See the official
[node status implementation](https://raw.githubusercontent.com/proxmox/pve-manager/master/PVE/API2/Nodes.pm).

Pool listings are filtered to storage on which the token has `Datastore.Audit`
or `Datastore.AllocateSpace`. An empty list therefore reports unknown visibility,
and a missing pool cannot be assumed nonexistent. See the official
[storage status implementation](https://raw.githubusercontent.com/proxmox/pve-storage/master/src/PVE/API2/Storage/Status.pm).

## Concrete full-deployment preflight

Template and global VMID checks still run first. Capacity checks run through
`check_scenario_resources` both during preflight and immediately before a full
runner starts. Configure and teardown keep their existing ownership checks and
do not require provisioning capacity.

`capacity_memory` adds declared VM memory overrides, falling back to the
template's reported memory. Missing free RAM blocks with
`SCENARIO_RESOURCES_UNREADABLE`; insufficient measured free RAM blocks with
`INSUFFICIENT_MEMORY`. Projected use at or above 90% of total RAM warns with
`MEMORY_PRESSURE`.

`capacity_cpu` reads template cores/sockets and applies the declared cores
override while retaining the template's socket count. More configured vCPUs than
logical CPUs warns with `CPU_OVERCOMMIT`. A current CPU sample at or above 90%
warns with `CPU_PRESSURE`. Unreadable template CPU settings or node CPU data
produce warnings. Existing VM allocations are not subtracted from hardware
counts to manufacture an exclusive CPU quota.

`capacity_storage` estimates full-clone volumes on each inherited template pool,
including non-CD-ROM attached disks, EFI/TPM volumes with declared sizes and
requested disk growth. Unreadable disks or volumes produce
`STORAGE_REQUIREMENTS_UNKNOWN`; unavailable target pools produce
`STORAGE_POOL_UNKNOWN`. Estimates above measured free space warn with
`STORAGE_ESTIMATE_EXCEEDS_FREE`, and projected use at or above 90% warns with
`STORAGE_PRESSURE`.

These are storage estimates, not exact allocation enforcement. The matched
controller uses full clones, but a runtime `proxmox_dest_vm_storage_name`
override can change the target pool. Thin provisioning, snapshots, cloud-init
metadata, compression and shared backing devices also change actual allocation.
The current manifest does not seal a destination storage pool, so the report
explicitly labels its inherited-pool assumption. See Proxmox's
[full-clone API implementation](https://raw.githubusercontent.com/proxmox/qemu-server/master/src/PVE/API2/Qemu.pm).

Measurements do not reserve resources or control external Proxmox writers. The
checks target one node; they do not perform automatic placement, replication,
or exact per-user quota enforcement. This provides the v1 replacement for the
capacity surface requested in backend issue #47.
