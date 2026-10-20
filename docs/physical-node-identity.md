# Record a physical node behind Proxmox target aliases

An API URL, target row or credential is not a physical capacity identity. The
optional node inspector establishes a retained server identity through a fixed
SSH capability and verifies that the selected HTTPS API presents the same transport leaf
certificate and the selected node’s generated certificate. Two registered targets can then refer to one retained node.
This is identity support; capacity reservation and environment preparation are
separate capabilities. Managed environments remain `execution_ready=false`.

## One-time installation enrollment

The installation operator installs a root-owned, inspection-only capability
using its canonical infrastructure workflow, a dedicated service account/key,
exact source host restrictions and verified SSH host keys. The service key must
never grant general shell/sudo or initialization. Its exact forced command takes
no command-line arguments and accepts only bounded JSON inspection requests.
No operator or participant private key is reused.

`RANGE42_PREPARATION_AGENTS_FILE` points to a protected server-side file:

```json
{
  "version": 1,
  "installation_id": "cccccccccccccccccccccccccccccccc",
  "ssh_config": "/run/secrets/preparation-ssh-config",
  "agents": [
    {"target_host_id": "host-a", "node_name": "node-a", "ssh_alias": "range42-preparation-node-a"}
  ]
}
```

The example installation ID is synthetic. Use the installation's retained ID in
both the agent policy and backend configuration. Configured aliases must start
with `range42-preparation-`; their node and target IDs must match a registered
target. Configuration and SSH files must be bounded regular private files owned
by root or the service UID, without links. The installer protects referenced
private keys/known-host files separately. CLI paths, SSH aliases and installation
IDs are never accepted from browser request bodies.

SSH uses the selected fixed alias, batch mode and explicit strict host-key
checking. Input is `{"action":"inspect"}`; output is limited to16KiB with a15s
deadline. The agent returns its retained node ID, hashed hardware binding,
approved node/installation, policy revision, boot ID, active API certificate
SHA256, generated node certificate SHA256 and inspection-only capabilities. Raw machine ID/hardware UUID and private
keys are excluded. Unknown protocol fields/capabilities and readiness assertions
are refused. The active proxy leaf takes precedence over a generated PVE leaf.

The backend then uses its existing verified Proxmox TLS configuration and saved
API credential to read `/api2/json/version`. It compares the actual TLS peer DER
fingerprint with the inspector's active leaf. It also reads the selected node’s
`certificates/info` and requires exactly one matching SHA256 witness for
`pve-ssl.pem`. This distinguishes nodes even when their proxy certificate is
shared. Another trusted certificate or a matching shared proxy leaf alone does
not qualify that logical node. A TLS-terminating proxy with a different leaf needs an
explicitly supported binding workflow; there is no insecure or hostname-only
fallback.

## Review in the existing environment setup

Choose a saved Proxmox target in Settings → Connections → native environment
setup. Load its recorded identity, inspect the node, review its node ID and API
certificate, then record the reviewed binding. This adds no workshop tab and
starts no VM operation.

Administrator-only routes are:

- `GET /v1/proxmox/hosts/{host_id}/physical-node`: local recorded state and
  configuration availability. `live_verified:false` is explicit; `needs_review`
  checks saved target/configuration drift, not current connectivity.
- `POST .../physical-node/review` with `{}`: fresh SSH/HTTPS observations and a
  review revision. Caller-supplied identity, readiness, paths or commands fail422.
- `POST .../physical-node` with only `review_revision`: repeat live observation
  and atomically record the unchanged proof. Approval uses the authenticated
  server principal. A lost reply can return the same retained binding without
  duplicating a node or reassigning the original approval.

All projections are typed and use `Cache-Control:no-store`. Missing/unhealthy
capability returns `PREPARATION_AGENT_UNAVAILABLE`(503); endpoint disagreement
returns `PHYSICAL_NODE_ENDPOINT_MISMATCH`(409); stale local review returns
`PHYSICAL_NODE_REVIEW_REQUIRED`(409). Private worker diagnostics are suppressed.
The UI clears stale drafts on target/backend/principal changes and refreshes
recorded state after a failed/lost save before offering another deliberate
inspection.

## Ownership and recovery

Migration0013 adds retained physical nodes and target bindings. Hardware bindings
are unique independently of URL/credential aliases. Recreated node IDs, replaced
hardware, changed retained observations and dangling aliases fail closed with
`PHYSICAL_NODE_IDENTITY_CONFLICT`. Network reads finish before the serialized
SQLite writer; the final target/registry checks are fresh. There is no automatic
identity transfer or deletion. Target rotation preserves its old recorded node
and requires inspection; a target cannot silently switch to another physical
server. Downgrade refuses retained nodes or bindings, including inconsistent
orphan state.

Preserve original node ownership and capacity commitments during recovery. Restore
or reconcile the original identity before a separately reviewed migration; do not
clear records to make a new capacity pool. Independent owned-resource cleanup
must remain available when inspection is unavailable.

## Verification and limits

Focused tests cover aliases, different credentials, concurrent/stale reviews,
lost replies, server approval attribution, corrupt retained state, guarded
migration, bounded transport and actual verified TLS. The optional
`RANGE42_PREPARATION_AGENT_SOURCE` fixture additionally compares the actual
installation agent's certificate reader with the real HTTPS test leaf.

Disposable browser acceptance uses the production-built UI, source API, actual
installed SSH/sudo agent and two verified-TLS synthetic read services. Canonical
Ansible check/apply/repeat and negative sudo/source-host/identity checks run in a
network-isolated container. This does not enroll or qualify a live PVE host,
reserve RAM/storage/vCPU, fence direct writers or prepare native infrastructure.

The read-only node certificate contract is checked against the [Proxmox certificate API](https://github.com/proxmox/pve-manager/blob/master/PVE/API2/Certificates.pm) and [certificate management documentation](https://github.com/proxmox/pve-docs/blob/master/certificate-management.adoc).
