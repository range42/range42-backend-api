# SDN CLI, API and UI parity audit — 2026-09-10

This is a source audit, not a claim that every operation has been exercised on the shared hypervisor. Remote branch refs were fetched without resetting integration worktrees:

| Repository / branch | Reviewed revision |
| --- | --- |
| [range42-playbooks / feat-sdn-implementation](https://github.com/range42/range42-playbooks/tree/55dcfd5d4f8e49ace4f6e40ee034ae67a2f0f9c5) | `55dcfd5d4f8e49ace4f6e40ee034ae67a2f0f9c5` |
| [Proxmox controller / feat-sdn-implementation](https://github.com/range42/range42-ansible_roles-proxmox_controller/tree/c1713569d2396ea91b2e55fe1348d402613254a6) | `c1713569d2396ea91b2e55fe1348d402613254a6` |
| [Debug devkit / feat-sdn-implementation](https://github.com/range42/range42-ansible_roles-debug-devkit/tree/5125cd9b92ccdf72cc0bc7f646dc3bff3f54bd9f) | `5125cd9b92ccdf72cc0bc7f646dc3bff3f54bd9f` |

## Function and state matrix

Bundle paths below are relative to `bundles/`. Every Proxmox bundle requires the selected workspace vault/inventory and verified access to its configured host. Bundles that reconcile SNAT additionally need the `proxmox_cli` host group. The upstream firewall bundles scope guests by manifest IDs, but do not verify the backend's `range42-deployment:<id>` description marker.

| Capability | Upstream contract and side effects | Current UI/API parity | Safe next integration |
| --- | --- | --- | --- |
| Declare SDN | `proxmox/sdn_network.bootstrap`; `BUNDLE_SDN_ZONE`, `BUNDLE_SDN_VNETS=[{vnet,subnet,gateway?,snat?}]`; simple zone; creates/reconciles and applies | Concrete UI authors `scenario_networks.json`, defaults to SDN, exposes outbound NAT; v1 preflight checks conflicts and pending changes | Keep host provisioning lock and manifest preflight. Shared VNet identity is cluster-wide; no automatic shared-network deletion |
| Change live outbound NAT | `proxmox/sdn_network.internet_on`, `.internet_off`, `.internet_toggle`; only `BUNDLE_SDN_SUBNET_ID`, read actual VNet/CIDR/state first | No deployment v1 runtime operation or UI control. Authoring checkbox affects deployment input; changing an existing subnet conflicts with current preflight | Prefer explicit desired state, not toggle. Check selected deployment's declared subnet plus current identity; block unrelated pending SDN changes; serialize whole host apply; read state afterward |
| Reconcile live SNAT | All three internet bundles at `55dcfd5` reconcile every declared subnet after apply; target takes requested state, others retain their individual declarations | Not represented as a separate API operation | Report host-wide scope. Primitive deletes surplus rules; it does not create a missing live rule. Confirm declaration and live forwarding separately |
| Host firewall | `firewall/in_proxmox/firewall.enable.datacenter_and_nodes` posts management accepts first, arms DC then node, rereads both; disable turns DC off then node, retains accepts | v0 raw DC/node enable/disable calls exist; no composite v1/UI workflow | Host/cluster operator action, never implied by a deployment VM toggle. Show DC-wide effect; use composite bundle with locked host mutation and readback |
| Single VM firewall | `firewall/in_proxmox/firewall.enable.vm` / `.disable.vm`; required `BUNDLE_VM_ID`, optional `BUNDLE_VM_VMNET_ID`; default covers all NICs; enable posts SSH accept, flags NICs, arms VM, reads state | v0 raw VM switch exists; graph NIC firewall flag is not complete enforcement | Require exact deployment ownership; execute composite sequence; show DC switch, VM switch and each NIC flag independently |
| Scenario firewall | `firewall/in_proxmox/firewall.enable.vms` / `.disable.vms`; reads active `scenario/manifest/scenario_vms.json`, excludes templates/absent VMs, phases rules/cards/switches | No v1 scenario operation/UI control | Resolve actual owned VM set first; refuse ID collisions instead of trusting manifest membership. Preserve absent/failed VM results, report partial success |
| Anti-lockout baseline | `firewall/in_proxmox/firewall.baseline.management_access` protects 22/tcp and 8006/tcp at DC and node; rule placement precedes covering denies; never arms switches | No composite API/UI action | Keep baseline before any host arming. Current role predicates are not a general firewall policy proof: they explicitly do not verify source-restricted accepts. Do not advertise guaranteed reachability for arbitrary custom rules |
| VM service baselines | `firewall/in_proxmox/firewall.baseline.*` (SSH/HTTP/deployer/Gitea/Wazuh etc.); explicit bundle params in descriptors | Library discovery includes these; attachment execution needs pinned runtime provenance | Render only declared parameters; configure existing owned VMs at the documented stage. Preserve service install flags and parameter naming |
| Guest OS firewall | `firewall/in_vm/os_firewall.baseline.*`; `target_group`, `software.configure.firewalls` role and `firewall_rules`; guest-side SSH/become | No complete graph-to-runtime guest firewall workflow | Resolve group membership from owned scenario; track guest UFW separately from Proxmox firewall; require role/catalog dependency availability |
| Firewall status | `firewall/in_proxmox/firewall.report.status`; read-only DC/node plus deployed manifest guest switches/NIC flags | No consolidated v1 state/UI view; raw v0 lists are narrower | Safe first live hook: read-only deployment status with effective prerequisites and unavailable-state errors |
| Aliases and rule objects | Controller has DC and VM alias list/add/delete; `dc_fw_alias_*` / `vm_fw_alias_*`; rules accept source/destination strings | v0 exposes only VM alias/rule primitives; graph rules are local configuration, not object-aware resolved policy | Keep upstream objects work separate. Need agreed namespaces, ownership, references, dependency ordering, rename/delete semantics and unresolved-reference validation before exposing an object policy editor |
| Filtered execution logs | Debug devkit JSON config has `display_skipped_hosts=False`; callbacks exist; latest branch adds richer firewall verdicts | Backend runner/SSE uses its own event pipeline, not the CLI callback configuration | Offer a UI filter for skipped/include noise while retaining complete event history/download. Do not drop events at persistence boundary or count filtered output as task success |

## Pilot scenario ordering and defaults

`scenarios/blank_scenario_2_sdn/main.yml` imports firewall status and management access before infrastructure; SDN bootstrap before templates; all VM bootstrap stages before VM configuration; VM SSH baselines after VMs exist; optional final arming at the end. `FIREWALL_ARM_VMS=YES` opts in; default is NO. Preserve this default when importing the scenario.

`manifest/feature_flags.yml` declares optional WAZUH, MISP, DEPLOYER_UI, GITEA, MATTERMOST, NEXTCLOUD, ROCKETCHAT and TAILSCALE, currently false by default. The manifest is a catalogue of possible VMs rather than proof that all were deployed. The scenario's declared networks intentionally permit inter-subnet routing; outbound SNAT and firewall arming must not be labelled inter-team isolation.

The scenario has both a runtime legacy bridge workaround and a distinct durable cleanup bundle. The durable cleanup modifies hypervisor interface configuration and is intentionally not imported by normal scenario deployment. Do not expose it as an automatic repair during onboarding.

## Release and ownership boundary

Integration checkpoints: playbooks `7497514` includes bootstrap capability metadata and upstream `55dcfd5` through a normal merge; controller `7b11ddd` includes reviewed PR103/105 and the tested storage-aware PR101 ISO portion. Six real Ansible/local TLS bootstrap cases, four controller template/cache cases and four NAT bundle orchestration cases passed. NAT tests substitute the controller boundary and therefore do not attest to live host iptables or guest connectivity.

Before a runtime mutation endpoint is considered complete, it needs shared bearer authorization, exact host/deployment target resolution, source/runtime provenance, operation locking, a typed allowlist of actions and parameters, preserved anti-lockout sequence, bounded output and state readback. For cluster/shared-network changes the operation must expose the actual shared scope. Reusing raw v0 routes does not provide these deployment guarantees.

The bundle metadata index is usable for discovery, but selecting a source/path cannot silently execute a same-named bundle from another installed revision. Executable attachments require a backend-enforced revision/content and dependency contract. That contract is the next implementation slice; arbitrary private-source execution is not currently provided by catalog detail responses.
