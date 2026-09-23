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
and toggle operations are rejected. Administrator host/datacenter and scoped
policy operations are described in [scoped runtime operations](scoped-runtime-operations.md). NAT
acknowledgment is required because SDN apply is cluster-wide. Under the reviewed
native contract, the selected subnet takes the requested state and other subnets
retain their prior live SNAT state. The older marker-based contract instead
reconciles other subnets to their declarations. The UI describes the detected contract.

Each accepted request becomes a normal durable attempt with `scope: runtime`.
The ordinary attempt-create DTO cannot request that scope or supply operation
metadata. The response exposes read-only `operation` intent containing the
typed request, pinned project/host identity, requested API URL and node name,
and installed runtime fingerprint. Re-registering a host with another address
or node invalidates queued operations. Launch reloads the host registration;
normal and recovered completion cannot verify results against a replacement
target. Older operations without an explicit target binding must be requested
again; their recorded history remains readable.
It uses the same reservation predicate, workspace lock, cancellation, events,
process identity and restart recovery as deployment attempts. Runtime mutations
also hold the installation's shared provisioning lock, including in the
detached runner.

Before launch, the backend verifies the installed runtime profile, approved
bundle capabilities, exact deployment VM ownership and protected VMID ranges.
It checks these again after SSH/vault preparation. Ansible then rereads every
target guest's exact ownership marker, name and template flag before importing
the reviewed composite. Runtime inventory binds the API and SSH controller to
the deployment's selected Proxmox host. Before any SNAT composite starts, a
read-only delegated hostname check must match the selected Proxmox node; a
cluster API endpoint pointing at another node is refused. Register the target
node's own API address for these SSH operations. Scenario sweeps include only verified
owned guests; confirmed absent IDs remain in the result. Any foreign or
unreadable guest blocks the sweep before mutation.

VM enable imports the upstream composite that posts SSH acceptance, sets every
NIC firewall flag, and finally arms the VM switch. Disable uses its inverse
sequence and retains SSH acceptance. The datacenter and node switches are
observed separately and never changed by guest firewall operations. A configured guest
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
  readback. Matching the API declaration alone cannot produce success. Final
  readback must also confirm no pending or unreadable global SDN changes.
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
see [bundle attachments](bundle-attachments.md). The application recognizes the
unchanged native playbooks `6dcf31b5b53600f41be1ce7553d489dba4ad803b` and controller
`617b57cddecbe4ddedd1f72becc7023bbcdf2114` by the actual bundle and selected role
tree digests (`native-sdn-20260921`). No new capability files in those repositories
are required. The first role in Ansible's search path must match. Unknown or
changed sources remain unavailable; a revision label alone is insufficient.

Native NAT operations first read the selected node's POSTROUTING chain and refuse
source-scoped rules other than the supported simple SNAT/MASQUERADE shapes.
Negation, comments, extra predicates and source-scoped non-NAT jumps are refused
before the composite. Source-free rules are outside the native sweep. This
application guard limits the native source-only reconciliation to its supported
inputs. External writers still require coordination. After the composite, a
separate native list action supplies the node-bound NAT rule count; the native
deletion count or its pre-apply read cannot alone establish completion.

Legacy installations can still advertise `bundles/runtime-capabilities.json`.
That NAT contract additionally requires
`snat_reconciles_all_declared_subnets: true`. The first controller role selected
by `ANSIBLE_ROLES_PATH` must also advertise `snat_rule_matching:
exact_source_nat_target_v1` in its `runtime-capabilities.json`. That version
counts only actual SNAT/MASQUERADE jumps for the exact non-negated source CIDR;
ACCEPT/LOG rules and quoted comments cannot satisfy or be removed by NAT
reconciliation. Completion requires the same semantic marker, requested desired
state and selected node in the controller's readback. Older source-only counts
remain unverified. Read-only status remains available
when runtime mutation capabilities are absent. A changed profile requires a
new operation request; an already reserved operation cannot silently execute
against another release.

`GET /v1/proxmox/runtime-capabilities` is available to viewers and returns the
verified contract, operation list, bootstrap feature list and whether this
installation permits scenario management-rule preparation. Unavailable support
is explicit and does not expose local source paths. See [native scenario
integration](native-sdn-integration.md) for authoring and validation details.
