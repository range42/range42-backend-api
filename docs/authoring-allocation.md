# Durable scenario authoring reservations

The authenticated authoring API reserves stable VMIDs and configured IPv4 addresses before the first scenario Git save. A local UI project ID is sufficient; no `projects` database row is required. It performs read-only Proxmox requests and writes an expiring Range42 ledger. It does not create VMs, SDN networks, or DHCP leases.

Run `alembic upgrade head` before starting an upgraded API. Migration `0005_allocation_reservations` follows `0004_runtime_operations`; the installed playbooks must be configured with `API_BACKEND_WWWAPP_PLAYBOOKS_DIR`. The SQLite database and its backups contain the reservations.

## API and ownership

Generate a cryptographically random URL-safe token of 32–128 characters once per local authoring project and retain it in browser storage. Send it in `X-Range42-Reservation-Token` alongside the normal API bearer token. Keep the reservation token out of Git, exported project files, URLs, and logs. Only its SHA-256 digest is stored in the ledger. Shared API authentication still defines the operator trust boundary; reservation tokens prevent accidental cross-project lease changes, and do not create separate user accounts.

`POST /v1/proxmox/hosts/{host_id}/reservations` creates, updates, or renews one lease:

```json
{
  "project_key": "local-draft-2f6d",
  "lease_seconds": 3600,
  "vmid_start": 2000,
  "vmid_end": 8999,
  "networks": [
    {
      "network_id": "training",
      "bridge": "training",
      "subnet": "10.42.8.0/24",
      "gateway": "10.42.8.1",
      "reserved_ips": ["10.42.8.254"]
    }
  ],
  "vms": [
    {"node_id": "workstation", "nics": [{"index": 0, "network_id": "training"}]},
    {"node_id": "server", "vm_id": 2050, "nics": [{"index": 0, "network_id": "training", "ip": "10.42.8.20"}]}
  ]
}
```

The response contains `reservation_id`, `project_key`, `host_id`, `node_name`, `expires_at`, `checked_at`, `assignments`, and human-readable `limitations`. Every assignment has `node_id`, `vm_id`, and `nics`; each NIC includes `index`, `network_id`, `bridge`, `subnet`, `ip`, `prefix`, and optional `gateway`.

Explicit IDs and addresses are preserved or rejected with a collision error. Omitted values retain the active lease's assignment for that node and NIC when its network ID, bridge, and subnet match. Other omitted values are allocated in ascending order. Manual and retained assignments are claimed before automatic ones, independently of request ordering. Removing a VM/NIC from a successful update releases its old assignment atomically. A failed update preserves the previous lease unchanged.

Repeated requests with the same project and token are safe after a lost response. Each successful POST repeats the Proxmox checks and renews the lease. An expired lease may be reclaimed by another project; sending the old project and token does not guarantee the old mapping remains available. The UI should show the returned mapping and lease expiry before Apply, and retain explicit selected values on subsequent edits. Never silently replace manual values after a conflict.

`GET /v1/proxmox/hosts/{host_id}/reservations/{reservation_id}` returns the saved lease without renewing or repeating live checks. `DELETE` at the same URL releases an owned lease and returns 204. Both require the reservation header. A missing, expired, or previously released lease returns `ALLOCATION_EXPIRED`; DELETE retries after a lost successful response can therefore treat that error as already released. Losing the token requires waiting for expiry. No administrator token-recovery endpoint exists.

## Collision checks and bounds

SQLite `BEGIN IMMEDIATE` serializes ledger planning and writes across API workers. HTTP requests never hold that writer lock. If another author claims a candidate during a Proxmox read, the allocator replans and checks its replacement before saving. VMIDs and `(bridge, IPv4 address)` pairs are reserved conservatively across the entire installation, including host aliases. This also prevents reuse across truly separate clusters registered in the same API; explicit cluster identity support is future work.

The allocator excludes default and host-specific protected VMID ranges, visible cluster VMIDs, other active leases, subnet network/broadcast addresses, gateways, explicit `reserved_ips`, host interface addresses, and QEMU cloud-init/LXC NIC addresses. Both current and pending guest configuration views are inspected, including secondary NICs and guests on other cluster nodes. VMID protection cannot be weakened by host overrides. Addresses duplicated across NICs on a bridge are rejected; declared subnets within a request must not overlap.

Before the Proxmox audit, the API reads the installed `scenarios/_reserved.json`
ledger and every current `*/manifest/scenario_vms.json`. It reserves their union,
including templates and secondary NIC addresses, even when those guests do not
exist yet. A stale aggregate cannot release an old reservation or hide a new
manifest. Missing, malformed, oversized or symlinked reservation files stop a
new allocation; release remains available. File reads run outside the event
loop and outside the SQLite writer lock. This covers the installed source
snapshot, not arbitrary unlinked Git repositories or future external edits.

Each source is limited to 2 MiB, with 16 MiB total, 1024 manifests and 16384
combined entries. Existing duplicated source declarations remain reserved;
this union does not certify that the upstream catalog passes its own collision
checker. The checked SDN integration snapshot currently has a stale aggregate
and pre-existing VMID/address conflicts at 1186 and 1187. Those source conflicts
are retained conservatively and require separate upstream cleanup.

Each candidate VMID receives `/cluster/nextid?vmid=...` confirmation. That endpoint checks the cluster inventory regardless of the permission-filtered resource list. Only its exact occupied-VMID response is treated as occupied; unexpected responses and permission/network failures stop allocation. Proxmox itself describes this assertion as availability at the instant of the check, not a reservation. See [the official cluster API implementation](https://raw.githubusercontent.com/proxmox/pve-manager/master/PVE/API2/Cluster.pm).

Address inspection requires propagated `VM.Audit` at `/vms`, readable guest configurations, and readable host network configuration on every cluster node. Effective permission values describe propagation, as defined by [the official access API](https://raw.githubusercontent.com/proxmox/pve-access-control/master/src/PVE/API2/AccessControl.pm). A `current` query selects running versus pending container values in [the official LXC configuration API](https://raw.githubusercontent.com/proxmox/pve-container/master/src/PVE/API2/LXC/Config.pm).

Limits are 64 VMs, 32 declared networks, 32 NICs per VM, 256 NICs total, 256 explicit reserved addresses per network, IPv4 prefixes /16 through /30, and 1000 active leases. Leases last 300–86400 seconds; the default is 3600. A request scans at most 100000 candidate IDs locally, 4096 global availability probes, and the first 4096 usable addresses of each subnet. The full remote audit is bounded to 30 seconds, 4096 guests, 128 cluster nodes, and eight concurrent requests. Expired rows are reclaimed during successful allocation writes. IPv6, automatic replication, and multiple target nodes per lease are unsupported.

Errors use the normal Range42 envelope. `ALLOCATION_OWNERSHIP` is 403; strict schema/header failures are 422. `ALLOCATION_OCCUPIED`, `ALLOCATION_PROTECTED`, `ALLOCATION_POOL_EXHAUSTED`, `ALLOCATION_OCCUPANCY_UNAVAILABLE`, `ALLOCATION_INVALID`, `ALLOCATION_EXPIRED`, and `ALLOCATION_BUSY` are normally 409. A busy ledger can be retried with the same project/token. Occupied manual values require a deliberate edit; permission and connectivity errors require correcting the host setup.

## Operational limits

The lease coordinates authors using this API. It does not lock Proxmox, independent CLI writers, other Range42 installations, or manual scenario files. Deployment preflight remains responsible for checking current VMID ownership and availability. Release an authoring lease after a successful deployment or let it expire; renewing it once its VMs exist returns an occupancy conflict.

Address checks inspect configuration, not live guest traffic. Static addresses configured inside guests, custom cloud-init network files, DHCP ranges, access-restricted guests, and external devices may be absent from that view. Use declared `reserved_ips` and authoritative external IPAM for those addresses. A successful response must not be presented as proof that an address is globally unused. Existing deployments need their normal ownership-aware configure/teardown workflow; this API allocates new authoring resources.

Validation covers actual SQLite restart persistence and independent-engine concurrency, retry ownership, protected and hidden occupied VMIDs, cross-NIC/manual collisions, current and pending address exclusion, release/expiry, failed-update rollback, browser CORS, and upgrading/downgrading the schema. No test mutates a live Proxmox host.
