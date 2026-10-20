# Desired run identities and memberships

Administrator identity management is available inside deployment Overview for a
saved native installation workshop. It uses an optional provider-neutral platform
runtime. It does not create login accounts, issue VPN keys, confirm provider
success or mark a workshop ready. Existing fixed-cohort management remains
separate; the new routes do not use its username namespace or root worker.

`GET /v1/deployments/{deployment_id}/identities` returns the immutable run review,
valid participant seats and the protected registry's current desired state.
`POST` on the same path accepts the reviewed `registry_id`, `expected_revision`,
`run_revision` and one operation:

- `scope.register` initializes the roster with the saved deadline and the
  saved number of participant places per environment (one by default).
- `person.register` associates a run alias with a new temporary/permanent person,
  or an explicitly selected existing person reference.
- `membership.set` assigns that person a participant/facilitator/director/observer
  role and an optional valid run seat. A participant must have a seat.
- `membership.remove` removes this run's desired role while retaining the person
  and their other run memberships.

The backend derives scope, deadline and participant limit from the saved plan;
the browser cannot override them. The runtime serializes reviewed revisions and
seat occupancy under its own writer. Competing/stale requests receive409. Its
`IDENTITY_SEAT_OCCUPIED` response explains that another assignment must be removed
or another seat chosen. Do not automatically replay a failed or timed-out change:
read current durable state, review it, then make a new deliberate request.

Only installation administrators can read or change this registry. Authentication
provides the actor; body fields cannot impersonate it. Responses have no-store
headers and typed projections that omit unexpected credential fields and raw
adapter payloads. Run roles convey no backend, IdP or VPN administration.

## Optional installed runtime

Configure `RANGE42_IDENTITY_SSH_CONFIG` to an absolute, protected regular SSH config
owned by root or the backend service account, with no group/other write access.
The fixed alias is `range42-identities`. Use a dedicated key, pinned host key and
a non-root service account restricted to the installed dispatcher command and
its private state directory. Disable forwarding and interactive access. The
backend never selects a remote command or caller-supplied path. It requires host
key verification, bounds request/output size and limits each request to15seconds.
The private runtime must match wire version1 and support the accepted scope
participant policy. Credential replacement, provider installation and subject
binding remain trusted integration work.

No runtime is installed or enabled by setting this environment variable alone.
Unconfigured/unavailable installations return503 rather than inventing success.
Backend/UI release qualification, canonical service account/forced-command
installation, provider ownership verification and active revocation are separate
acceptance requirements. Existing cohort integrations are unaffected by these routes.

The real dispatcher wire test can be enabled with
`RANGE42_IDENTITY_RUNTIME_SOURCE=/path/to/range42-exercise-platform`. It exercises
the actual private module in a separate process over the same bounded protocol;
this optional source test does not prove SSH deployment. Separate disposable
browser acceptance uses actual host-key-verified localhost SSH, synthetic records
and the production-built UI. Neither proves real IdP/VPN provisioning or laptop
connectivity. Public CI does not need private repository access.

## Optional integration boundary

Environment planning, resource reservation, provisioning, group reset and
retirement do not require an identity dispatcher, Keycloak or Headscale.
`GET /v1/deployments/{deployment_id}/identities/capabilities` is an administrator
read of local installation configuration; it performs no SSH or provider call.
The same installation-wide review is available without a workshop at
`GET /v1/integrations/identity`, presented in Deployer UI under
Settings → Integrations. This is configuration visibility, not an account
provisioner or a browser control for installing arbitrary adapters.
It reports `not_configured`, `configured` or `invalid_configuration`, with
`optional: true`, `required_for_environment_deployment: false` and
`provider_access_verified: false`. Configuration is not a provider health check.
It exposes neither config paths nor credentials and sends no-store headers.

The Overview roster checks this capability before contacting the dispatcher.
An absent integration displays its optional status and hides unavailable roster
actions. An enabled integration failure remains visible and never silently
switches identity stores. Environment execution still requires its independent
infrastructure, ownership and capacity checks. An unavailable VPN integration
does not imply that laptop reachability or network isolation is ready.

Keep selected identity and network adapters in the installation integration
service, behind the existing bounded protocol. The core owns saved run/group
policy and validates requested scopes. Adapters own provider-specific subjects,
credentials, reconciliation and confirmed revocation. Configuration must select
reviewed, installed adapters; project or catalog content cannot load arbitrary
Python modules, shell commands or provider credentials. Keycloak and Headscale
are installation choices, not mandatory dependencies of public Range42 packages
or CI. This change adds capability reporting, not provider installation or a
plugin loader. Managed onboarding and provider setup remain separate work.

## Shared group environments

The workshop input `seats` counts environments. Optional
`participants_per_environment` is a strict integer from1 through10, default1.
Five environments with two participants each still allocate ten guests,
100GiB RAM,30vCPUs and960GiB potential virtual disks. A larger group does not
multiply VMs. Original default-one compiled plans omit the new field so existing
immutable resource plans and receipts stay compatible.

The backend derives the roster's per-team limit from the saved plan, never from
an onboarding request. The optional registry enforces that limit atomically;
the UI shows environments as full only when all places are occupied. Removing
one person's membership preserves their partner's desired membership and does
not reset the shared environment. Resetting an environment affects the whole
group. These desired-state rules do not provision individual operating-system,
Proxmox, IdP or VPN accounts.
