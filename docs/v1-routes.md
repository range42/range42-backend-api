# /v1 route map

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/catalog/sources` | list git sources |
| POST | `/v1/catalog/sources` | register source |
| DELETE | `/v1/catalog/sources/{id}` | remove source |
| POST | `/v1/catalog/sources/{id}/refresh` | rescan repos |
| GET | `/v1/catalog/entries` | cross-source browse |
| GET | `/v1/catalog/entries/{source}/{path}` | entry detail |
| GET | `/v1/projects` | list projects |
| POST | `/v1/projects` | create project |
| PATCH | `/v1/projects/{id}` | patch project |
| POST | `/v1/projects/{id}/compose` | compose effective doc |
| POST | `/v1/projects/{id}/validate` | validate base+overlay |
| GET | `/v1/deployments` | list deployments |
| POST | `/v1/deployments` | create deployment + workspace |
| GET | `/v1/deployments/{id}` | get deployment |
| DELETE | `/v1/deployments/{id}` | teardown (confirm-phrase) |
| GET | `/v1/deployments/{id}/attempts` | list attempts |
| POST | `/v1/deployments/{id}/attempts` | enqueue attempt |
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
