# Imported guest configuration

The authenticated v1 API can review and edit five configuration fields on one
explicitly registered Proxmox host and guest. The existing raw `GET .../config`
import endpoint is unchanged. These new routes call Proxmox directly with that
registration's encrypted-at-rest credential; they do not use the global v0
inventory or Ansible/controller code.

## Review and apply

`GET /v1/proxmox/hosts/{host_id}/vms/{vmid}/config/review?vmtype=qemu`
returns:

```json
{
  "host_id": "selected-host-id",
  "node": "pve-b",
  "vmid": 60001,
  "vmtype": "qemu",
  "digest": "<opaque 64-character lowercase hexadecimal review digest>",
  "target_digest": "<opaque 64-character lowercase hexadecimal target digest>",
  "current": {"name":"guest","description":"","cores":2,"memory":2048,"tags":""},
  "configured": {"name":"guest","description":"","cores":2,"memory":4096,"tags":""},
  "pending": ["memory"]
}
```

`vmtype` is exactly `qemu` or `lxc`; the default is `qemu`. Each values object has
exactly the five shown keys. Missing name/CPU/memory values remain null; absent
description/tags are empty strings. Known numeric PVE representations are
normalized; malformed responses are refused. The new routes never return raw
cloud-init credentials, SSH keys, other config keys, or upstream error bodies.

`current` is the Proxmox current configuration, **not measured guest state**.
`configured` includes pending changes. `pending` lists differences within these
five fields only. A restart, resource hotplug or guest cooperation can still be
required; this endpoint never starts/stops/reboots a guest to apply changes.
Proxmox documents the `current` option and config-file digest in its
[QEMU API source](https://github.com/proxmox/qemu-server/blob/master/src/PVE/API2/Qemu.pm)
and [LXC API source](https://github.com/proxmox/pve-container/blob/master/src/PVE/API2/LXC/Config.pm).

After explicit review, send a partial patch:

```http
PUT /v1/proxmox/hosts/{host_id}/vms/{vmid}/config?vmtype=qemu
Authorization: Bearer <backend token>
Content-Type: application/json

{"digest":"<unchanged review digest>","changes":{"description":"Reviewed guest","tags":"lab;exercise"}}
```

The digest binds the host registration (including API URL, node, credentials and
protection policy), VMID, guest type and Proxmox config digest. It is opaque to
clients and does not grant authorization. `target_digest` covers the same target
binding and guest identity without the PVE config digest: it remains unchanged
after ordinary configuration changes. Capture it at review and require the same
value in every fresh readback before confirming an edit. A stale review returns HTTP 409
`VM_CONFIG_STALE` before dispatch. The actual PVE SHA1 is passed to its write API
to enforce the config-file check under Proxmox's own lock if an external edit
occurs after the review. Clients must preserve desired edits and review again.

| Field | Accepted edit |
| --- | --- |
| `name` | 1–63 ASCII DNS-label characters; starts/ends with a letter or digit; hyphen allowed inside. Maps to LXC `hostname`. |
| `description` | Up to 8192 characters; empty clears. Tab/newline allowed; other ASCII control characters and deployment ownership markers refused. |
| `cores` | Strict integer 1–128; QEMU cores **per socket**. Sockets are unchanged. |
| `memory` | Strict integer 16–4194304 MiB. No capacity guarantee or reservation is implied. |
| `tags` | Up to 1024 characters; unique semicolon-separated tags of 1–64 characters, `[A-Za-z0-9_][A-Za-z0-9_.+-]*`; empty clears. |

At least one field is required. Nulls, coercion, extra fields, disks, NICs,
firewall/SDN, boot/cloud-init options, credentials, deletion/revert flags and
arbitrary PVE arguments are not supported.

## Result semantics

HTTP 200 returns `{status, upid, review, reason}`; unused fields are null:

| Status | Meaning and next step |
| --- | --- |
| `configured` | PVE returned null completion and a fresh consistent readback matches every requested configured value. `review` contains that readback, including pending values. This does not claim guest-effective resources changed. |
| `accepted` | PVE returned a validated `qmconfig` UPID for this original node and VMID. Poll the existing host-bound task endpoint using the same original host/backend context and `expected_target_digest`; require stopped/OK, then refresh and compare target/configuration before claiming success. |
| `unconfirmed` | A write may have occurred, but transport/response/readback did not establish its result. Preserve desired edits, refresh/review, and **do not blindly retry**. A mismatching readable review is included when available. |

Fixed `reason` values are `write_outcome_unknown`, `unexpected_write_response`,
`readback_unavailable`, and `readback_mismatch`. Upstream freeform errors are not
returned. Permission rejection returns HTTP 403 `VM_CONFIG_FORBIDDEN`. A lost
HTTP response or disconnected caller is also an unknown outcome and needs
readback before any new submission.

For configuration task polling use
`GET /v1/proxmox/hosts/{host_id}/tasks/{upid}/status?expected_target_digest=<review.target_digest>`.
The backend checks the registered target binding, UPID node, `qmconfig` worker
kind and concrete VMID before contacting PVE. Host URL, node, credential or
protection-policy re-registration causes HTTP 409 `VM_CONFIG_TARGET_CHANGED`;
the old task result must not be attributed to the replacement target. Malformed
digests return 422. Existing task callers without this optional query retain
their previous behavior. The optional guard applies specifically to QEMU
configuration workers; other task kinds are refused with this query.

QEMU CPU/memory edits use PVE POST (recommended for possible hotplug); QEMU
metadata and LXC edits use PVE PUT. Both synchronous null and async UPID outcomes
are handled without inventing a task ID. See the
[official generated API schema](https://pve.proxmox.com/pve-docs/api-viewer/)
and QEMU source's `update_vm_api`/`background_delay` completion paths.

## Safety and limits

The shared backend bearer authenticates the operator; the selected PVE token
must have `VM.Audit` and the field-specific edit permissions enforced by PVE
(CPU, memory, options and tag privileges as applicable). This adds no per-user
RBAC or broader rights than the registered token. Protected VMIDs (including
host-specific added ranges), templates, locked guests, any deployment ownership
marker, and VMIDs in the committed deployment allocation ledger are refused.
The latter remains protected if an external writer removed its marker.

Writes hold the existing installation provisioning lock and SQLite writer
transaction through checked dispatch/readback, preventing local host
re-registration or claim transfer during that request. There are no database
changes. The lock is released on every return/failure. An accepted PVE task can
outlive the HTTP request; its UPID remains bound to the original node, and PVE
owns its task/config lock. This endpoint creates no durable deployment attempt
or cross-cluster task transaction. External PVE writers still need coordination;
the digest check is not a cluster-wide reservation.

Review errors use `VM_CONFIG_UNAVAILABLE` for incomplete/malformed upstream
responses, `VM_CONFIG_STALE` for inconsistent snapshots, `VM_CONFIG_FORBIDDEN`
for permission failure, and specific `VM_CONFIG_TEMPLATE`, `VM_CONFIG_LOCKED`,
`VM_CONFIG_MANAGED`, `VMID_PROTECTED` or `PROVISIONING_BUSY` refusal codes.

Per-VM snapshot create/delete now also enforce the existing protected-VMID
guard, matching rollback. This does not implement durable scenario snapshot
sets or broaden snapshot ownership semantics.

Validation is local HTTP/SQLite plus simulated PVE transport, including actual
SQLite writer contention and provisioning flock contention. No shared guest or
live Proxmox configuration was changed for this source validation.
