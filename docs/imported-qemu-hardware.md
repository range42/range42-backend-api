# Reviewed imported QEMU hardware edits

This isolated source slice adds operator-only review and conditional edits for
one existing NIC or one existing data disk. It is not installed or live-tested.
The existing five-field QEMU/LXC configuration editor remains separate.

- `GET /v1/proxmox/hosts/{host_id}/vms/{vmid}/hardware/review` returns parsed current
  and configured hardware, pending device identifiers, and opaque target/config
  digests. Passwords, keys and raw volume paths are excluded.
- `PUT …/hardware/nics/{nic_id}` accepts `{digest,changes}`. Changes are limited to
  bridge, VLAN tag (including explicit removal), Proxmox NIC firewall and link
  disconnection. The original readable MAC, model and other options are retained.
  Unknown/ambiguous NIC identities and missing devices refuse the write. A changed
  bridge must be verified active on that exact node via `network?type=any_bridge`.
- `PUT …/hardware/disks/{disk_id}/grow` accepts `{digest,size_gb}`. The requested
  absolute size must exceed the readable current size. The existing storage pool
  must have verified available bytes for the increase. This does not reserve
  storage or grow a guest partition/filesystem. Relative `+` sizes, extra disks,
  shrinking, volume moves, CD-ROMs, EFI/TPM and unused disks are not exposed.

Both write paths acquire the provisioning lock and DB transaction, recheck target
registration, deployment claims/markers, protected VMIDs, templates, PVE locks and
snapshot-member activity, then compare the fresh digest. Pending changes on the
selected device must settle first. Unrelated device configuration is preserved.
PVE still enforces actual token privileges and may refuse a change.

NIC writes use PVE's asynchronous config POST; growth uses its digest-guarded
resize PUT. Returned `qmconfig`/`resize` tasks are bound to the exact node and
VMID. The browser waits for successful task completion and fresh configured
readback, retaining a clear distinction from pending or guest-observed state.
Unconfirmed dispatch/task/readback never retries automatically. The review form
preserves typed intent on failure and invalidates on target/backend/credential
changes; an equal target object replacement does not invalidate it.

The ConfigPanel entry requires a QEMU guest imported with an explicit registered
host ID. Hardware edits do not rewrite authored canvas edges, scenario NIC/IP
assignments or immutable deployment history. Reconcile the authored topology
separately after live edits. This slice does not expose LXC hardware changes,
device creation/deletion, MAC/model edits or existing-guest cloud-init changes.

Local validation uses the actual FastAPI/SQLite route with simulated PVE, focused
Vue tests, and production desktop/mobile browser fixtures. The first browser run
caught equal-target-object invalidation; its regression and fix are included.
No provider, guest, SDN or controller mutation is involved in these checks.

The primary [PVE API schema](https://pve.proxmox.com/pve-docs/api-viewer/) defines
config POST for hotplug/storage work and the absolute-size/digest resize request.
The upstream [resize worker change](https://lore.proxmox.com/pve-devel/20230530135207.87705-4-f.ebner%40proxmox.com/t/)
documents the `resize` task type. Schema/source observations used during this
implementation are local references, not evidence that this slice ran on PVE.
