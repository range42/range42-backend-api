# Deployment runtime controls

Authenticated operators can inspect a pinned concrete deployment with
`GET /v1/deployments/{id}/runtime`. The response includes the project revision,
observation time, datacenter and node firewall switches, each declared guest's
ownership status and VM/NIC firewall flags, and declared SDN subnet identities.
Unreadable values remain unknown. A guest missing from a permission-filtered
resource list is reported absent only after the cluster confirms its VMID is
unused. The endpoint performs no Proxmox writes.

`POST /v1/deployments/{id}/operations` accepts one of these exact bodies:

```json
{"kind":"vm_firewall","vm_id":3191,"enabled":true}
```

```json
{"kind":"scenario_firewall","enabled":false}
```

```json
{"kind":"sdn_snat","vnet":"r42blue","enabled":false,"acknowledge_shared_scope":true}
```

Booleans must be JSON booleans. Undeclared fields, arbitrary bundle paths,
toggle operations, and host/datacenter firewall actions are rejected. NAT
acknowledgment is required because SDN apply is cluster-wide and the upstream
bundle reconciles surplus live SNAT rules for all declared subnets on the host.
Other subnets retain their own declared desired state.

Each accepted request becomes a normal durable attempt with `scope: runtime`.
The ordinary attempt-create DTO cannot request that scope or supply operation
metadata. The response exposes read-only `operation` intent containing the
typed request, pinned project/host identity and installed runtime fingerprint.
It uses the same reservation predicate, workspace lock, cancellation, events,
process identity and restart recovery as deployment attempts. Runtime mutations
also hold the installation's shared provisioning lock, including in the
detached runner.

Before launch, the backend verifies the installed runtime profile, approved
bundle capabilities, exact deployment VM ownership and protected VMID ranges.
It checks these again after SSH/vault preparation. Ansible then rereads every
target guest's exact ownership marker, name and template flag before importing
the reviewed composite. Runtime inventory binds the API and SSH controller to
the deployment's selected Proxmox host. Scenario sweeps include only verified
owned guests; confirmed absent IDs remain in the result. Any foreign or
unreadable guest blocks the sweep before mutation.

VM enable imports the upstream composite that posts SSH acceptance, sets every
NIC firewall flag, and finally arms the VM switch. Disable uses its inverse
sequence and retains SSH acceptance. The datacenter and node switches are
observed separately and never changed by these endpoints. A configured guest
firewall with a disabled datacenter switch does not provide active filtering.
The current composite protects SSH on port 22; custom policies, alternative
management ports and guest OS firewall behavior need separate review. Disabling
NIC firewall flags also removes the MAC anti-spoofing behavior coupled to them.

NAT mutations require an active declared zone/VNet/CIDR/gateway match and no
pending SDN changes in any cluster family, including controllers and supported
fabric/routing objects. External Proxmox writers must coordinate separately;
the backend lock serializes writers in this installation. These operations do
not edit Git manifests. A later full deployment may therefore require updating
the authored NAT declaration to match the intended live setting.

Completion rereads state and persists `operation_result` before `attempt_end`:

- `observed`: the same firewall/guest/SDN observations returned by the GET route.
- `desired_reached`, `partial`: whether all or some requested targets match.
- Firewall operations: `matched_vmids`, `mismatched_vmids`, `missing_vmids`.
- NAT operations: `live_snat_rule_count` from the controller's host-side
  readback. Matching the API declaration alone cannot produce success.
- `live_forwarding_verified` remains false: rule presence is not an end-to-end
  connectivity test. An unavailable readback carries a safe error and fails the
  attempt rather than inventing success.

The result also appears in the canonical log stream as
`log_line.payload.runtime_result`. Partial sweeps retain terminal state
`partial`, including after backend recovery. SQLite migration
`0004_runtime_operations` adds nullable JSON intent/result columns while
preserving older attempts.

The installed release needs `RANGE42_BUNDLE_RUNTIME_MANIFEST` generated using
the exact exported playbooks/controller/catalog/collections and Ansible paths;
see [bundle attachments](bundle-attachments.md). Its playbooks must include
`bundles/runtime-capabilities.json`. NAT additionally requires
`snat_reconciles_all_declared_subnets: true`. Read-only status remains available
when runtime mutation capabilities are absent. A changed profile requires a
new operation request; an already reserved operation cannot silently execute
against another release.
