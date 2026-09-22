# Reviewed SDN and firewall operations

This extends the existing deployment runtime controls using the unchanged native
SDN sources documented in [native SDN integration](native-sdn-integration.md).
Only the backend API and deployer UI are changed. The new operations require the
verified `native-sdn-20260921` source trees; older or unrecognized installations
keep their existing supported operation list.

## Review, authorization and execution

Administrators first send a typed request to
`POST /v1/deployments/{id}/operations/plan`. The response includes the exact
pinned project revision, target host/node, affected resources, native steps and
`review_fingerprint`. The same request, with this fingerprint and
`acknowledge_shared_scope: true`, goes to `/operations`. A changed target,
configuration, attachment inventory, permissions or installed runtime invalidates
the review. No Proxmox write occurs during planning.

The administrator-only kinds are `host_firewall`, `sdn_network`, `firewall_rule`
and `firewall_alias`. Viewers can inspect reports; operators retain the existing
guest/NAT operations and the new read-only `runtime_observe` attempt. Capability
and permission information is returned separately in `/runtime`.

Every accepted operation uses the existing durable runtime attempt, reservation,
workspace/provisioning locks, cancellation and recovery path. The runner checks
again immediately before launch. Generated Ansible guards reread reviewed scope
hashes before native mutations; rule and alias edits also use the Proxmox digest
at the write itself. The installation lock does not lock out external Proxmox
writers, which must coordinate shared SDN apply separately.

Native runtime wrappers load an empty private vars file. Registered credentials
and reviewed native arguments are passed as backend-owned extra variables;
scenario vault settings cannot add an unreviewed VLAN, gateway or rule option.
The original vault and project remain unchanged.

## Network lifecycle

```json
{"kind":"sdn_network","action":"create","vnet":"r42blue","acknowledge_shared_scope":true}
```

`action` is `create` or `delete`; zone, CIDR, gateway and desired NAT come only
from the pinned scenario declaration. Creation requires an absent VNet and
creates its zone only when absent. It writes `range42-deployment-{deployment_id}`
as the VNet alias. Deletion requires that exact marker, matching zone, one exact
subnet/gateway and no VM/LXC/template attachments anywhere in the cluster.
Complete cluster audit and SDN allocation permissions are mandatory. Network
lifecycle currently requires a single-node cluster: a shared apply on other
nodes cannot be authorized without their native NAT preservation and readback. Unreadable
or filtered inventory never proves safe deletion. Pre-existing networks,
including networks made by an earlier full bootstrap without this marker, are
not adopted or deletable through this operation.

Both actions apply once through the native bundle and reconcile the selected
subnet. Shared zones are never deleted. The wrapper captures live counts for all
known SDN and legacy-interface IPv4 subnets, including zero-count neighbors, and preserves other sources'
prior counts after apply. Unsupported or mixed native NAT shapes are refused.
Final readback must verify the selected identity/absence, clean pending state,
selected live NAT count and preservation of other source counts. A partially
completed mutation remains visible with recovery guidance; no automatic rollback
or retry hides its outcome.

A legacy bridge with the same name, overlapping subnet or unrelated pending SDN
change blocks the plan. Bridge migration requires a separately reviewed operator
workflow; ordinary deployment cleanup never removes these objects.

## Host switches, policies and aliases

`host_firewall` accepts explicit `enabled: true/false`. Enabling uses native
management accepts on TCP 22 and 8006 before enabling the datacenter and selected
node switches. Disabling retains those accepts. The DC switch affects unrelated
nodes and guests. Custom management ports are outside this bounded contract.

`firewall_rule` supports `create`, `update`, `delete` and `move` in `vm`, `node`
or `datacenter` scope. Guest scope requires an exactly owned pinned VM. Creation
requires a stable `name` and a complete rule (IN/OUT, ACCEPT/DROP/REJECT, TCP/UDP,
numeric destination ports, optional CIDR/alias source and destination, enabled).
New rules are inserted first. Update/delete/move require a zero-based `position`;
move also takes the desired final `move_to`. A complete update explicitly clears
omitted optional predicates rather than retaining invisible restrictions.

Edits, reordering and deletion only affect deployment-marked, supported,
non-management rules. Outbound rule removal/editing is protected because it can
cut off management replies. Host/DC deny rules and guest management-port denies
are refused. Existing foreign and management rules remain protected.

`firewall_alias` supports `create`, `rename` and `delete` at DC or owned VM scope;
Proxmox does not have node aliases. Create requires `name` and canonical `cidr`;
rename requires `name` and `new_name`. Rename is a single native update retaining
CIDR and ownership comment. Rename/delete require deployment ownership and no
references in rules or IP sets. DC aliases are checked across every node, guest
and security group. References must be updated explicitly before renaming; they
are never silently rewritten.

## Reports and saved intent

`GET /v1/deployments/{id}/runtime-report` has a typed version-1 OpenAPI response:
firewall switches, declared/live SDN identity and pending state, ordered rules and
aliases, and per-NIC filtering prerequisites. Scope errors preserve readable
scopes and leave missing evidence unknown. Digest fields and upstream response
bodies are excluded. The node firewall switch is shown separately from guest
filtering prerequisites.

`{"kind":"runtime_observe"}` records a read-only native NAT observation in a
normal attempt. The report attaches the latest observation for the same pinned
revision and target identity, with its timestamp and attempt ID. This is
historical evidence, not a claim that state is still current. Every report keeps
`traffic_verified: false`; configuration and rule presence do not prove actual
forwarding or filtering.

The paired UI displays these distinctions and administrator review, preserves
policy drafts after refusal and records sanitized runtime intent/results in the
project's existing Git history. Runtime operations do not rewrite the saved
scenario; a subsequent deployment can restore declared settings or recreate a
removed declared network.

## Validation and release gate

Focused tests exercise permission/review refusal, exact ownership, native alias
references and digests, rule ordering, partial reports and durable readback.
Real Ansible execution tests cover TLS review drift, native bundle sequence,
vault isolation and unrelated NAT preservation. The UI browser fixture checks
desktop/mobile review and refusal, accessibility and preserved policy drafts.
These are controlled fixtures, not live guest qualification.

API #74 remains the shared-context deployment/traffic acceptance gate. On 22
September 2026 the shared API was reachable but lacked the new capability
endpoint; the available backend SSH keys were refused. Installing the paired
application branches and validating real traffic remains pending working backend
access. No host configuration was changed for these checks.
