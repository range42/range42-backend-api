# Reviewed native provisioning environments

An administrator can register a dedicated Proxmox target and reusable workshop
bounds in Settings → Connections → Native provisioning environments. Deploy
links to these settings when no workshop profile is configured. This uses the
existing backend host registration and native lifecycle; it does not install a
second orchestration service or require a particular identity/VPN provider.

The backend must already have an installed native runtime with an explicit node
and verified HTTPS API binding in its trusted context registry. The native backend installer records its authenticated registered target in
`<workspace>/.range42-target.json` (version1, target host ID, node and HTTPS API
origin). Automatic context discovery reads this credential-free private binding;
it refuses malformed, oversized, linked or group/world-writable files instead of
falling back to an unbound context. An explicit `RANGE42_NATIVE_CONTEXTS_FILE`
registry remains authoritative. Earlier installations without a binding retain
legacy CLI discovery, but cannot register reusable provisioning environments
until the installer records their binding. The installer preserves a matching
binding and refuses to overwrite a different one; retargeting needs a reviewed
migration. This does not qualify physical infrastructure.

The browser
selects that runtime by ID; it cannot submit filesystem paths or shell commands.
By default, a dedicated target credential may have a different registered host
ID but must identify the same node and HTTPS endpoint. An explicit reviewed
runtime reuse option, described below, supports another retained physical node. Native workshop execution uses that
selected credential, including when an older workspace vault contains another
Proxmox identity. The original context and its files remain unchanged.

The form supplies an environment name, profile ID, permitted guest VM ID range,
private participant pool, network count, first bridge number, source template
IDs and storage. Review expands the pool into exact /28 subnet/gateway/bridge
pairs. Save requires the same review revision. The backend repeats review and
refuses changes to target credentials, protected VM ID settings, runtime files
or the submitted bounds between review and save. Optional retained gateway references
are observed through the selected target: current and pending ownership, VM
generation, bridge/interface and MAC must match. New references are checked again
on review and save; unrelated guest fields and credentials are never returned. Credentials remain encrypted
on the existing target record; they are not duplicated in the environment table
or returned by these endpoints. The UI clears entered credentials on submission
and when the active backend changes, and does not persist them in browser data.

Environment versions are immutable. Register a new version to change bounds or
target placement. A credential/runtime change makes an existing environment
unavailable until an administrator reviews and revalidates it. Revalidation
preserves its ID and resource bounds; it cannot move an existing environment to
a different node or API endpoint. Deleting a referenced target returns409 and
preserves the binding. Populated environment state also blocks downgrade.

## API

All endpoints below require the installation-wide administrator role. Normal
context discovery remains available through the existing `/v1/contexts` route.

| Method and path | Behavior |
| --- | --- |
| `GET /v1/environments` | Registered versions, current planning readiness and trusted runtime choices. No runtime filesystem paths or credentials. |
| `POST /v1/environments/infrastructure/review` | Observe selected managed gateway VM IDs on this profile’s participant bridges. |
| `POST /v1/environments/review` | Review one `EnvironmentSelection`; return its revision and explicit preparation requirements. |
| `POST /v1/environments` | Persist the reviewed selection with `review_revision`. |
| `POST /v1/environments/{id}/review` | Review the current inputs of an existing immutable version. |
| `POST /v1/environments/{id}/revalidate` | Accept the reviewed credential/runtime revision while preserving target placement and bounds. |

## Optional environment bridge preparation

The normal catalog foundation scenario can prepare its registered environment's
bridges in the same native attempt as its two reusable template copies. Select
**Also prepare the environment's isolated bridges and gateway management network**
in Deploy. The native request adds `prepare_environment_networks: true`; omitted
or false preserves the template-only workflow and its serialized legacy shape.
This option requires a registered environment with a managed gateway network.

Reserve and physically accept the foundation's original capacity first. In
Overview, retain the template preparation request, then review and retain the
bridge request. Run the existing full deployment action. It prepares templates,
then invokes the separate bounded network module, and independently reconciles
both services before reporting completion. It creates no parallel allocator,
queue, participant credentials or identity-provider dependency.

| Method and path | Behavior |
| --- | --- |
| `GET /v1/deployments/{id}/foundation/networks` | Read retained environment network intent, readiness and any current local blocking issue. |
| `POST /v1/deployments/{id}/foundation/networks/review` | Recompile the exact environment-owned plan, independently observe the optional module, and durably retain the original request. Empty body only; no native network mutation. |
| `POST /v1/deployments/{id}/foundation/networks/reconcile` | Read original node status and retain its independently verified receipt. Empty body only; no native network mutation. |

All three use the existing administrator policy and return `Cache-Control:
no-store`. Reviewing requires the original accepted foundation request, selected
target, physical identity, approved environment profile and current separate
network service. Core stores its journal in the original `NativeEnvironment`
row; the original capacity request and physical/template service revision stay
unchanged. Migration `0021_environment_networks` adds one nullable column and
refuses a downgrade that would discard populated ownership.

Installations enroll the optional `range42_environment_network_agent` role
separately. Its protected backend registry is selected by
`RANGE42_ENVIRONMENT_NETWORK_AGENTS_FILE`, using the existing v2 per-target
registry shape and a dedicated `range42-env-network-*` SSH alias/configuration.
Do not modify the original physical/template SSH configuration when adding it.
Each alias uses strict host-key verification and the separately restricted
no-argument root dispatcher; no browser request can select a command or path.
The module's own policy revision and transport revision gate only its stage.

Bridge readiness does not qualify participant execution. Each run still owns
its own gateway and participant guests, and requires gateway configuration,
scoped temporary egress, original generation evidence, capacity acceptance and
isolation checks. Participant onboarding remains optional and separate.

`EnvironmentSelection` contains `label`, `base_context_id`, `target_host_id` and
`workshop_profile`. The version1 profile has `id`, `vmid_ranges`, `networks`
(`bridge`, `subnet`, `gateway`), `proxmox_template`, `deployer_template`, `storage`
and optional generation-bound infrastructure NIC exceptions. See the generated
OpenAPI for exact constraints. Infrastructure exceptions are protected operator
input and are not included in public profile projections.

## Readiness boundary

Registration enables planning. Responses deliberately report
`infrastructure_qualified: false`. Managed versions are workshop-only and cannot
be used for an unbounded native scenario. Preflight and the attempt dispatcher
both block guest creation/configuration until infrastructure qualification is
available. Owned cleanup remains allowed; its original VM generation checks
still apply. Credential revalidation preserves these immutable pins and remains
possible while a gateway is offline. Live preflight still needs source template,
bridge, guest ownership and capacity evidence; laptop access, routing, DNS and
browser trust require the selected access integration. This form does not
create or certify bridges, gateways, templates or VPN enrollment. Their accepted
native preparation lifecycle and live qualification remain separate work.

This is an administrator configuration workflow. It does not add project/team
tenancy or claim that an installation-wide operator is a limited facilitator.

## Replace a stored target credential

Settings offers an explicit credential replacement for an existing registered
target. It reviews the current target first, then sends only the replacement
credential and the review revision. This preserves the target ID, node, API
address, token scope, bridge defaults and protected VM IDs. It does not issue or
revoke a Proxmox token; obtain the replacement through the selected installation’s
approved Proxmox access workflow. Syntax is validated, but storing a credential
does not establish live connectivity or privileges.

`POST /v1/proxmox/hosts/{host_id}/credential/review` returns a credential-free
host projection and opaque revision. `PUT /v1/proxmox/hosts/{host_id}/credential`
accepts `expected_revision` and `token_ref` only. Both endpoints are administrator
only. The persistent transaction serializes review comparison and replacement;
stale reviews and extra target fields are refused. The stored credential remains
encrypted, cached health is cleared, and affected environment versions require
review/revalidation with unchanged resource bounds. This deliberately avoids the
installation bundle’s broader name-based host re-registration API for a human
credential-only operation. Entered replacements are cleared after submission,
cancel, component disposal or backend change and are never saved in browser
settings. A local input typo can be corrected without obtaining a new review;
a failed submitted replacement requires a new review before retrying.

## Optional gateway owned by each run

An approved profile may include `managed_gateway` with `management_bridge` and
`management_subnet`. The subnet is a separate aligned RFC1918 /29; its first
usable address belongs to the physical node. This mode is mutually exclusive
with a retained shared gateway or infrastructure NIC exceptions.

Settings → Connections offers **Prepare one managed gateway per run**. Saving
this policy authorizes planning only. Deploy adds a gateway VM ID and management
address to the ordinary workshop form. The default gateway ID follows the
participant pairs; the operator chooses an unused management address from the
remaining five hosts. The backend validates exact profile bounds and reserves
all resources with the existing allocation service. A conflicting VM ID or IP
still blocks the reservation.

The compiled plan retains `participants` and `guests` as the original participant
mapping. Its optional `managed_gateway` is a separate owned VM, with one management
NIC and one gateway NIC per group network. `assignments` contains all of them in
one `DeploymentAllocation`. Capacity charges the gateway's 4 GiB RAM, two vCPUs
and 32 GiB disk alongside the participants. Five groups of two therefore reserve
11 VMs, 104 GiB RAM, 32 vCPUs and 992 GiB virtual disk. Preview and saved native
scenario preparation use the same topology projection, including every gateway
interface. Old plans without the option retain their original serialized shape.

The existing allocation request accepts an optional `gateway_node_id` for a
network. It must identify one explicit VM NIC at that network's declared gateway
address. This lets that VM reserve the gateway IP without weakening observed
occupancy, other reservations, reserved IPs or deployment ownership checks.
Automatic allocation continues to skip gateway addresses.

This increment does not create bridges, clone or configure a gateway, qualify
routing/isolation, or enable managed execution. Template preparation, native
materialization and verified retirement remain required before a real run.
Keycloak, Headscale and participant onboarding remain independent integrations.


## Reuse the installed runtime for another reviewed node

Settings offers **Use this installed runtime for the selected reviewed node**.
It defaults to off. Enable it only when reusing the installation's native code
and keys for a separately registered target. The backend requires that target's
previously reviewed physical identity, unchanged target credential/API/node and
matching protected agent configuration. It reads the existing enrollment; it
does not initialize an agent, execute SSH, or infer a fresh hardware observation.
Inspect and record the selected physical node first when this evidence is absent
or stale.

Review includes the selected physical-node ID and name. Save retains its physical
identity, hardware binding, node and installation ID in that immutable environment
version. Changed configuration invalidates planning until reviewed again; changed
physical identity cannot be accepted by ordinary revalidation. A replacement
credential requires the physical-node review and then unchanged-scope environment
revalidation. The original context, registry, workspace and source files remain
unchanged.

The environment resolves to its selected target while reusing the original
runtime's code and protected credential/key locations. Native execution already
renders an attempt-private repository and inventory. The selected API credential
supersedes any earlier Proxmox credential in the workspace vault, and a bound
target's API authority/address now take precedence over old inventory defaults.
The context's original target remains authoritative for existing deployments.

The request adds strict boolean `reuse_runtime: true`; omitted/false retains the
original same-target review digest and behavior. Responses include optional
`runtime_target` with credential-free retained identity. Migration0019 adds the
nullable `runtime_target_json` column, preserves older review stamps and refuses
downgrade while these pins exist.

This option enables bounded workshop planning only. It leaves `execution_ready`
and `infrastructure_qualified` false. Source-template preparation, gateway/network
isolation, materialization and retirement still need their native qualification.
It does not copy an installer or introduce an identity/VPN provider dependency.
