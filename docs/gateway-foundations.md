# Review a retained gateway before preparing participant ports

Use Settings → Connections → Native provisioning environments to select the
existing gateway for a new environment. Its future participant bridges do not
need to be attached yet. Inspection records the gateway's name, owner, original
VM generation, complete port list and configuration revision. It compares full
current and pending configuration before accepting that snapshot.

The form shows the supported 32 NIC slots, existing use, additional participant
ports and remaining space. For example, 14 existing ports plus 10 future ports
fit; 14 plus 20 do not. Reduce the network count or choose another gateway when
there are insufficient slots. The gateway must be outside the participant VMID
ranges and separate from both source templates.

Review configuration, then save the environment. Recorded environment scope can
be reopened from its list entry. Changing inputs requires a new inspection; a
changed gateway generation/configuration prevents saving the stale review. If a
save response is lost, the list is refreshed and the old proof is cleared. A
repeat of the same still-current reviewed configuration returns its original
environment ID, including concurrent requests, rather than creating duplicates.

These controls register desired bounds and retained identity. They do not
create bridges, attach ports, qualify filtering, issue credentials, reserve RAM
or enable native execution. Gateway preparation, persistent physical node
identity, durable capacity admission and trusted qualification receipts still
need their own native lifecycle. The recorded foundation is separate from the
participant NIC exceptions used by already prepared legacy profiles.

## Backend contract

`POST /v1/environments/foundation/review` accepts the normal environment selection
and one `vmid`. It returns `gateway_foundation`, server-derived `nic_capacity`
and `infrastructure_qualified:false`. The reviewed foundation can then be sent
as optional `gateway_foundation` in environment review/registration requests.
It contains no guest passwords or API credentials. Administrator authentication
and normal mutation auditing apply to this read-only Proxmox inspection.

The old `/infrastructure/review` contract remains available for previously bound
participant interfaces. An environment must select a supported binding mode;
recording a foundation does not authorize it to reuse arbitrary existing NICs.
Registration re-observes the exact foundation before saving. Final local review
and idempotent insertion are serialized without holding the SQLite writer during
Proxmox HTTP reads.

Migration 0012 adds nullable foundation state, preserving previous review stamps
and credentials. Omitted foundations are excluded from the legacy review hash.
Downgrade refuses while retained foundation records would be lost. Source tests
and disposable TLS-fixture browser checks do not establish live preparation or
participant connectivity.
