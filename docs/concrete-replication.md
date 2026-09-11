# Concrete replication contract

Replicated scenarios remain literal Git-pinned deployments. The UI expands an
explicit roster into VM manifest v3, network definitions, inventory and content
targets before publication. The backend validates the optional
`manifest/scenario_instances.json` file whenever it resolves a concrete scenario,
including preflight and execution. Ordinary concrete scenarios without this file
retain their existing contract.

Version 1 contains `scenario_id`, an `intent` roster and source scope maps,
`instances`, and `networks`. Scope is `shared`, `per_team`, or `per_user`.
Team IDs are unique, and user IDs are unique within each team. Stable IDs contain
1–128 ASCII letters, digits, dots, underscores or hyphens. Display labels are not
part of identity. Rosters are bounded at 64 teams and 64 total users; expansion is
bounded at 64 VMs, 32 networks and 256 NICs total, with 32 NICs per VM.

Each instance records its source node, optional team/user IDs, VMID, hostname and
ordered NIC mappings. Each network records the corresponding source/cohort plus
VNet or bridge name, subnet, gateway and SNAT. An instance key is `vm-` or `net-`
followed by the lowercase SHA256 of compact UTF-8 JSON
`[scenario_id, source_node_id, team_id, user_id]`; absent cohort values are null.
The backend derives the expected identities and counts from the roster and
requires exact equality, so an omitted user cannot silently become one shared VM.

Every declared VM must occur once in the literal VM manifest and scenario guest
inventory, with the same hostname and management IP. NIC indexes, stable source
edge keys, network mappings, prefixes and addresses must agree across all files.
Source NIC identities and connections must remain the same across replicas,
including parallel NICs. Only the primary NIC may use the network gateway.
Additional inventory hosts and duplicate host definitions are rejected.

A VM can connect to a shared network or its matching team/user network. It cannot
connect to another cohort or implicitly fan out from a shared VM into scoped
networks. Network instances require distinct names and nonoverlapping subnets.
SDN names remain limited to 8 characters. Existing bridges may use 15-character
names and require SNAT=false; the backend does not create or reconfigure those
bridges. Shared subnets still share outbound NAT policy.

Changing the instance manifest is a topology change: a content-only configure
revision must preserve it alongside the original VM/network manifests and
inventory. Manifest files must be bounded regular files within the scenario;
malformed or escaped files produce a generic error without echoing their content.

These declarations are public authored identities, **not allocation ownership**.
They do not authorize VM reuse, release a reservation, or prove that an existing
guest belongs to this deployment. Durable committed assignments, additive growth
and partial-deployment resume remain separate work. Explicit per-instance network,
VMID and address assignments are needed until that allocation integration exists.

The tests include real output from the UI compiler with three users across two
teams and two isolated team networks, plus rejected cardinality, identity, NIC,
inventory and cross-cohort mutations. Shared runtime deployment of replication
still requires the corresponding UI authoring release and end-to-end acceptance.
