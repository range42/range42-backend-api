# /v1 route map

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/catalog/sources` | list git sources |
| POST | `/v1/catalog/sources` | register source |
| POST | `/v1/catalog/sources/default` | register/reuse the public Range42 catalog |
| PATCH | `/v1/catalog/sources/{id}` | rotate or clear source credentials |
| DELETE | `/v1/catalog/sources/{id}` | remove source |
| POST | `/v1/catalog/sources/{id}/refresh` | rescan repos |
| GET | `/v1/catalog/entries` | cross-source browse |
| GET | `/v1/catalog/entries/{source}/{path}` | entry detail |
| GET | `/v1/projects` | list projects |
| POST | `/v1/projects` | create project |
| GET | `/v1/projects/{id}` | read registered project binding |
| PUT | `/v1/projects/{id}` | register/update a stable browser project identity |
| PATCH | `/v1/projects/{id}` | partially update project |
| POST | `/v1/projects/{id}/compose` | compose effective doc |
| POST | `/v1/projects/{id}/validate` | validate base+overlay |
| GET | `/v1/deployments` | list deployments |
| POST | `/v1/deployments` | create deployment + workspace |
| GET | `/v1/deployments/{id}` | get deployment |
| DELETE | `/v1/deployments/{id}` | execute pinned teardown.yml (confirm-phrase), retain history |
| GET | `/v1/deployments/{id}/attempts` | list attempts |
| POST | `/v1/deployments/{id}/attempts` | start full/configure/teardown attempt |
| POST | `/v1/deployments/{id}/preflight` | run preflight |
| GET | `/v1/deployments/{id}/preflight` | latest preflight |
| GET | `/v1/deployments/{id}/events` | SSE stream |
| POST | `/v1/deployments/{id}/cancel` | signal in-flight attempt SIGTERM |
| POST | `/v1/deployments/{id}/teams/{n}/reset` | per-team reset |
| POST | `/v1/deployments/{id}/snapshot` | take snapshot |
| POST | `/v1/deployments/{id}/rollback` | rollback (reject on expired) |
| GET | `/v1/deployments/{id}/snapshots` | list snapshots |
| GET | `/v1/deployments/{id}/timings` | per-stage/team durations |
| GET | `/v1/proxmox/hosts` | list hosts |
| POST | `/v1/proxmox/hosts` | register host |
| DELETE | `/v1/proxmox/hosts/{id}` | remove host |
| GET | `/v1/proxmox/hosts/{id}/health` | probe API+SDN |
| GET | `/v1/health` | liveness |
| GET | `/v1/health/ready` | readiness (sqlite+fs+proxmox+git) |
| GET | `/v1/admin/stats` | SSE counters |

`PUT /v1/projects/{id}` returns 201 for a new identity and 200 for an update.
The binding references a backend Source; credentials are never accepted as project
fields. Source/repository/subdirectory/branch strategy changes after a deployment
exists return `409 PROJECT_BINDING_IN_USE`; register a different project identity.

Concrete attempts select `main.yml`, `configure.yml` or `teardown.yml` explicitly.
Teardown requires `confirm_codename` on both the DELETE route and direct attempt
creation. Configure may supply a saved `project_sha`; its inventory and VM/network
manifests must match the deployment's original revision. Every new attempt reports
its effective `project_sha`. Snapshot, rollback and team reset return
`PROJECT_SCENARIO_SCOPE_UNSUPPORTED` for concrete deployments until implemented.
