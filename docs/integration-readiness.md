# Catalog, inventory and deployment readiness

For saved `range42-playbooks` scenarios and compatible private repositories, see
[native scenario projects](native-scenarios.md). That workflow selects an existing
deployer-cli context and preserves the native scripts, templates and shared files.

Repository onboarding prepares catalog and inventory content. Concrete scenario
playbooks and assets are now checked out from the pinned project commit. External
bundles, roles and target-host configuration still need to be available to the
backend runner.

## Direction: concrete scenario directories

`_universal` is retired. New deployment creation, preflight and execution reject
it with guidance to save a concrete scenario. Stored deployments retain their
history and logs. The UI authors concrete
`scenarios/<name>/` directories using the current bundle contract, including
`manifest/scenario_vms.json`, stage playbooks, `main.yml`, and a rendered
`hosts.yml`. The normal scenario runner and deployer CLI should consume the same
artifact.

SDN is the default for new UI-authored scenarios, as confirmed
on 2026-09-10. Network preparation belongs in the normal concrete scenario flow.
An existing bridge remains an explicit compatibility option for existing labs;
missing SDN support must produce an actionable blocker for an SDN scenario,
without silently changing its network mode.

The editor generates a concrete scenario from explicitly configured VM and network
nodes. Its preview includes network preparation, template cloning, guest inventory,
ordered file/script/playbook/bundle content and ownership-checked VM teardown. Save
commits these files together, registers the project with the selected backend and
pins deployment to that commit. The detail page exposes scoped preflight, execution
logs, configuration from a later saved commit and confirmed teardown.

The June design's wait for `feat-scenario-bundles-refactor` is no longer a
prerequisite: that branch is already contained in the playbooks `dev` history.
Use the current scenario layout and `bundle_parameters.json` descriptors,
including `bundles/proxmox/vm.bootstrap`. The older standalone playbooks
generator branch emits an earlier stage layout and needs validation before reuse.

## Concrete project execution

For an ordinary scenario with `project_sha`, the backend fetches that full commit
SHA from the registered project's repository. Inside the optional `Project.subdir`,
it requires:

```text
scenarios/<scenario_label>/
  main.yml
  hosts.yml
  manifest/scenario_vms.json
  ...relative imported playbooks, files, scripts and templates...
```

The manifest uses the existing `{ "vms": [{ "vm_id": 5000, ... }] }` shape.
VMs are already expanded at authoring time; this path does not multiply them by
`team_count`. The backend no longer provides the retired topology inventory
writer or implicit per-team VMID expansion. The internal `vmid.safety` check
accepts literal `requested` IDs and `host_overrides`; it does not infer them
from a topology. Preflight checks the same scenario files as the runner and checks
the manifest's actual VMIDs for duplicates and protected ranges. It validates
inventory syntax. Generated scenarios also declare networks and source templates:
preflight checks target network readiness/conflicts, template existence, free VMIDs,
available memory and ownership. Guest reachability and the complete Ansible
dependency tree are verified during execution, not inferred from these checks.

Each attempt gets its own checkout. Advancing the source branch or running another
preflight cannot change the files of an active attempt. Checkout or scenario
validation errors block the project path; they never fall back to a different
server-installed scenario. `RANGE42_ACTIVE_CONFIG_DIR` points to the deployment
workspace, where real Range42 bundles expect `secrets/default_vault.yml`. The
backend environment must provide `RANGE42_BUNDLE_DIR` and required roles/collections.

The [local content example](../examples/concrete-project/README.md) demonstrates
file copying, script execution, a second Ansible playbook and assertions. Real
Ansible integration tests exercise success and failure, and verify that the
deployment keeps its pinned content after the repository branch advances.

Concrete attempts support `full` (`main.yml`), `configure` (`configure.yml`) and
`teardown` (`teardown.yml`). Configure and teardown require their explicit
entrypoints and never fall back to provisioning. Teardown requires the exact
codename confirmation and preserves workspace files and attempt history. Team
reset, snapshot and rollback reject concrete requests until runner implementations
exist. Active attempts and workspace locks prevent overlapping starts.

Configure can apply a newly saved commit through the attempt's `project_sha`.
Before execution, the backend compares its canonical `hosts.yml`, VM manifest and
optional network manifest with the deployment's original commit. Content may
change; target definitions must remain identical. The deployment retains its
original SHA, and each new attempt records the effective SHA. Existing databases
need Alembic migration `0003_attempt_project_sha` before using this API version.
See [runner lifecycle](runner-lifecycle.md) for state persistence and the remaining
restart recovery boundary.

Runtime `proxmox_api_*` and `proxmox_node` values come from the selected backend
host, overriding committed inventory and older vault credentials. Generated SSH
inventory uses `r42_proxmox_address` and `r42_proxmox_ssh_user` (default `root`,
configurable through backend `RANGE42_PROXMOX_SSH_USER`). Existing workspace keys
are unlocked through the SSH agent. A workspace admin public key, when available,
provides `default_admin_vm_ci_ssh_key`; the known-hosts path stays in the workspace.
Configured vault files are preserved. When no vault exists, the runner receives
a temporary empty mapping for bundles that unconditionally load the vault path;
it is removed before the workspace lock is released. Real VM provisioning still
needs the administrator's guest SSH key and required role dependencies.

The backend preserves a configured guest password from the workspace vault. If
none is configured, it generates a random password and persists it privately at
`secrets/guest_admin_password` with mode `0600`, reusing it for that deployment.
This overrides the upstream bundle's known fallback. The runtime value is included
in event redaction, including when a playbook prints it inside another message.
Configured values support encrypted vaults, inline `!vault` values and references
within the workspace vault. Password and API-token scalars are passed to Ansible
as literal values so characters resembling Jinja expressions cannot change them.

Unpinned installed scenarios retain the configured
`API_BACKEND_WWWAPP_PLAYBOOKS_DIR/scenarios/<label>/main.yml` path and workspace
inventory. This installed-scenario path excludes retired `_universal` scenarios.
Browser projects register their selected Source,
repository and optional subdirectory through idempotent `PUT /v1/projects/{id}`.
The backend rejects credentials in this payload and prevents changing a binding
used by an existing deployment. Renaming the project remains supported.

The same checked-out files can be invoked with ordinary `ansible-playbook`, using
their `hosts.yml`, the matching SDN bundle/controller revisions and the workspace
vault/SSH agent. Supply a private runtime variables file with the selected
`proxmox_api_*` values, `proxmox_node`, `r42_proxmox_address`,
`r42_proxmox_ssh_user`, `deployer_cli_user_ssh_known_hosts` and a stable
`r42_deployment_id`. The workspace must also supply the guest public key and an
explicit guest password; direct CLI invocation does not run the backend's random
password preparation. Keep the deployment identity unchanged for subsequent configuration or
teardown. The existing `range42-context deploy` wrapper still expects scenario
shell scripts; the UI emitter currently generates direct Ansible entrypoints.
This wrapper discovery is a remaining CLI integration task, not a different
scenario format.

## Git authoring and publication

Git-bound UI projects now save versioned files on a dedicated working branch,
and deployment pins that saved commit. Publishing is a separate action with
one or more configured destinations. Each destination selects its Git provider,
repository, target branch and direct-push or pull-request mode. For example, the
same component may open a public catalog PR and push to a private repository's
`main` branch. Provider permissions and branch protections apply independently;
repository visibility must not be used to impose an application permission rule.

GitHub, GitLab and Gitea are supported. The frontend previews the files and
selected destinations and reports each destination's result independently. GitHub,
GitLab and Gitea commit the snapshot atomically within each destination. A successful
destination is retained when another fails; retries use the same saved checkpoint.
Save and PR publication support verified personal forks when upstream write access
is unavailable. Review and merge happen on the Git provider. An organization fork
selector and in-app merging remain outside the implemented workflow.

New reusable items must materialize their executable content and standard metadata,
not just a canvas node. The public catalog's role convention is
`02_ansible_layer/admin/roles/<category>.<action>.<target>/`, with `tasks/main.yml`
and `meta/main.yml`. The new role form creates these files together with defaults
and a README, validates the task list and checks for naming collisions before
publication. The older `components/<type>s/<id>.json` browser inventory
format is not indexed by the current public catalog backend.

See the frontend's
[Git authoring workflow](../../range42-deployer-ui/docs/git-authoring-workflow.md)
for the current authoring and publication boundaries.

## SDN default: Hyde's separate branch contract

Hyde's active `feat-sdn-implementation` branches were inspected on
2026-09-10. The remote branch heads at that time were:

| Repository | Revision | Latest change |
| --- | --- | --- |
| [Proxmox controller](https://github.com/range42/range42-ansible_roles-proxmox_controller/tree/c1713569d2396ea91b2e55fe1348d402613254a6) | `c1713569` | 2026-09-08: guards, messages and reports |
| [Playbooks](https://github.com/range42/range42-playbooks/tree/0601d343577eaffd3193f532abc387089d0d3433) | `0601d343` | 2026-09-09: pilot deployment firewall stages |

The SDN playbooks expose `sdn_network.bootstrap` with `BUNDLE_SDN_ZONE` and
`BUNDLE_SDN_VNETS` (a list of `vnet`, `subnet`, optional `gateway` and `snat`).
The bundle creates or reconciles declared objects, performs one guarded cluster
apply, waits for its task to finish and reconciles SNAT rules. The generated
`00_networks.yml` imports this bundle directly. Install these separate SDN branch
revisions for the runner; integration does not wait for their merge into `dev`.
The inspected SDN branch is not yet contained in playbooks `dev`. Its current
zone type is `simple` and host-local; VLAN/VXLAN are outside this contract. The
bundle defaults omitted SNAT to true, so the scenario should persist the chosen
egress setting explicitly. VM bootstrap can bind NICs directly to the VNet
bridge without a separate attachment bundle.

`manifest/scenario_networks.json` records the zone, VNets, IPv4 subnets, gateways
and explicit SNAT choices. VM NICs reference declared networks. An explicit
`existing_bridge` mode bypasses SDN creation and requires active target bridges.
Network bootstrap precedes VM bootstrap and guest content. Files, scripts and
playbooks live in the concrete scenario with targets, order and parameters in Git.

Network checks distinguish planned creation from active matching networks. They
block name collisions, gateway/SNAT drift, overlapping SDN and host-interface
subnets, and pending cluster changes. Pending controllers are checked as well as
zones/VNets/subnets; supported fabrics, prefix lists and route maps are also checked.
Full SDN execution needs `SDN.Allocate` on `/sdn`, and on fabric-capable servers
`Sys.Audit` on `/nodes` to avoid filtered pending-node results. Checks run again
immediately before spawning Ansible. Configure requires active existing networks
and uses only `configure.yml`; teardown does not require guest-network readiness.
Runtime readiness comes from the node's dedicated SDN zone/VNet status API. On
the tested PVE 9.2 host, active SDN devices are absent from the ordinary interface
list, so interface-list membership is not used to infer VNet availability.

Generated VMs carry `range42-deployment:<id>` in their description. Configure and
teardown verify the exact marker, VM name, type and node. Teardown rechecks inside
Ansible, shuts down gracefully, waits for deletion and preserves all SDN networks.
When a VM is absent from the permission-filtered resource list, the global
`/cluster/nextid?vmid=...` check must confirm absence before full provisioning or
idempotent teardown can proceed. Protected VMIDs remain blocked independently.

The API checks follow the official [SDN configuration/apply implementation](https://github.com/proxmox/pve-network/blob/master/src/PVE/Network/SDN.pm)
and [cluster resource/VMID API](https://github.com/proxmox/pve-manager/blob/master/PVE/API2/Cluster.pm).

Remaining execution boundaries:

- The current emitter supports one NIC per VM, simple IPv4 SDN, existing templates
  and text content. Containers, router nodes, team replication and old attachment
  metadata are rejected until explicitly supported.
- Disk/storage capacity is not budgeted yet. Required bundles, collections, roles,
  cloud-init template and workspace SSH keys must be installed by the operator.
- API preflight is a point-in-time check. Hyde's current bundle does not hold the
  newer Proxmox global SDN lock across its writes and apply. Concurrent external
  SDN edits can race; use a controlled SDN change window until that contract gains
  transaction support. Teardown intentionally preserves shared networks.
- On hosts with legacy bridge `post-up` NAT commands, cluster apply can replay
  those commands. Hyde's guarded apply avoids repeating it for unchanged SDN;
  this integration does not sweep unrelated legacy NAT rules.
- Full retry does not overwrite an existing VM, including a VM left by a partial
  attempt. Configure an owned VM or tear it down before provisioning again.
- Snapshot, rollback and team reset have no concrete execution implementation and
  reject requests explicitly. See runner lifecycle for restart recovery limits.

## Integration verification — 2026-09-10

An isolated SQLite database and local API/UI processes were used to register the
public catalog over HTTPS without Git credentials. Refresh and browse both
returned **44 entries** (29 containers and 15 Ansible roles). The public `main`
revision inspected was `9155746b3a0f688febf2437632e4f5dca1cdc981`.

Chromium checks exercised default onboarding, the 44-entry catalog, loading a
registration in a fresh browser, and invalid repository URL feedback. There were
no browser page errors or horizontal overflow at 390px width. The recommended
catalog card passed the WCAG A/AA axe checks run against it.

The complete backend suite passed **728 tests**. A final redaction-before-truncation
regression and its affected watcher/redaction/real-runner checks then passed
**30 tests**. The UI suite passed **797 tests** across 102 files. The UI production build, backend Ruff check,
OpenAPI drift check, full UI ESLint and Git whitespace checks passed. UI ESLint
retains 28 existing warnings; Vite still reports a large application bundle.

A Chromium session exercised authenticated preflight, Start, live logs and the
persisted successful attempt for the local content example. Real Ansible copied a
file, ran a script and imported an additional playbook. A role generated by the
new form was recognized by the backend catalog indexer and executed successfully
by Ansible. Browser checks also completed private-Gitea-direct/public-GitHub-PR
publication against simulated provider APIs, verifying the private branch update,
public PR, unchanged public base branch and saved project checkpoint. Additional
browser checks verified connecting a fresh project, verified personal-fork
creation, atomic save, backend registration against that fork, pinned deployment,
and identical selected configure SHA in preflight and execution. Repository,
Scenario, Save, Publish and settings remain visible at both 320px and 390px,
without browser errors or horizontal overflow.

Provider writes in browser checks used simulated Git APIs. No real remote Git
fork, pull request, push or merge was performed.

Cross-repository Ansible validation also exposed the catalog's SSH wait task
using a raw SSH alias from the operator's local config. The task now uses
`ansible.builtin.wait_for_connection`, honoring inventory connection settings,
the deployment SSH agent and the generated jump command. A real Ansible
regression exercises a non-resolvable inventory alias through its declared
connection, and the live SDN deployment exercises the remote SSH path.

### Live Proxmox verification

An isolated local Git repository, backend database and private workspace drove
real Ansible through the backend against `pve01` on pve-range42 (PVE 9.2.11).
The UI's generated scenario used template `9901`, VM `3191` (`r42-ui-smoke`),
SDN zone `r42smoke` and VNet `r42smk` (`10.42.70.0/24`, gateway `10.42.70.1`, SNAT).
Both SDN repositories used the separate branch revisions listed above.

| Scope | Actual result |
| --- | --- |
| Full | Passed: created/applied SDN, cloned and booted the VM, connected through the generated SSH jump command, waited for cloud-init, copied a file, ran a script and imported an assertion playbook. |
| Configure | Passed: applied commit `bab539355a7afb6ac02be93a39111ab3007ad13b` to the existing VM. A separate SSH read confirmed the new file and processed output. |
| Teardown | Passed: checked ownership, shut down and deleted VM `3191`, verified the deletion task and retained the deployment history/workspace. |

The deployment kept its original commit
`068f112ef0611c89cd4937581adf28dd55ff277f`; each attempt recorded its effective
revision, completed with return code `0` and released its workspace lock. Full
deployment took about 321 seconds including cloud-init; configure took about
33 seconds and teardown about 13 seconds.

After teardown the original 47 VMs remained, with their identities and statuses
unchanged; protected VM `100` remained running. Zone `r42smoke` and VNet `r42smk`
were intentionally retained and remain available. Configuration and teardown did
not change those SDN objects. No permanent backend/UI service was installed on
the host by this isolated integration test.

The live artifact audit found known credentials in translated log text and nested
module results that bypassed the older field-specific redactor. Known-secret
redaction now traverses every string value in mappings/lists and includes JSON
display escapes, before log text is truncated. Audit rule IDs contain no secret prefix. Regression tests cover
the translated `payload.text`, URI authorization headers and escaped credentials;
all 4,301 captured runner events were replayed through the corrected pipeline
with no known credentials remaining. The isolated credential copies were removed,
affected raw artifacts were scrubbed, and the isolated database token was removed.
The final scan of 4,702 test files found no full or JSON-escaped credential values;
the original operator workspace was preserved.
