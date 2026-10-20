# Reviewed native provisioning environments

An administrator can register a dedicated Proxmox target and reusable workshop
bounds in Settings → Connections → Native provisioning environments. Deploy
links to these settings when no workshop profile is configured. This uses the
existing backend host registration and native lifecycle; it does not install a
second orchestration service or require a particular identity/VPN provider.

The backend must already have an installed native runtime with an explicit node
and verified HTTPS API binding in its trusted context registry. The browser
selects that runtime by ID; it cannot submit filesystem paths or shell commands.
A dedicated target credential may have a different registered host ID but must
identify the same node and HTTPS endpoint. Native workshop execution uses that
selected credential, including when an older workspace vault contains another
Proxmox identity. The original context and its files remain unchanged.

The form supplies an environment name, profile ID, permitted guest VM ID range,
private participant pool, network count, first bridge number, source template
IDs and storage. Review expands the pool into exact /28 subnet/gateway/bridge
pairs. Save requires the same review revision. The backend repeats review and
refuses changes to target credentials, protected VM ID settings, runtime files
or the submitted bounds between review and save. Credentials remain encrypted
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
| `POST /v1/environments/review` | Review one `EnvironmentSelection`; return its revision and explicit preparation requirements. |
| `POST /v1/environments` | Persist the reviewed selection with `review_revision`. |
| `POST /v1/environments/{id}/review` | Review the current inputs of an existing immutable version. |
| `POST /v1/environments/{id}/revalidate` | Accept the reviewed credential/runtime revision while preserving target placement and bounds. |

`EnvironmentSelection` contains `label`, `base_context_id`, `target_host_id` and
`workshop_profile`. The version1 profile has `id`, `vmid_ranges`, `networks`
(`bridge`, `subnet`, `gateway`), `proxmox_template`, `deployer_template`, `storage`
and optional generation-bound infrastructure NIC exceptions. See the generated
OpenAPI for exact constraints. Infrastructure exceptions are protected operator
input and are not included in public profile projections.

## Readiness boundary

Registration enables planning. Responses deliberately report
`infrastructure_qualified: false`. Live preflight still needs source template,
bridge, guest ownership and capacity evidence; laptop access, routing, DNS and
browser trust require the selected access integration. This form does not
create or certify bridges, gateways, templates or VPN enrollment. Their accepted
native preparation lifecycle and live qualification remain separate work.

This is an administrator configuration workflow. It does not add project/team
tenancy or claim that an installation-wide operator is a limited facilitator.
