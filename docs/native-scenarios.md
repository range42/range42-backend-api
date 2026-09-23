# Native scenario projects

Native projects deploy saved `range42-playbooks` scenarios, forks, and private repositories with the same contract. They retain the full repository, native stage ordering, shared bundles and lifecycle scripts. They do not require a generated `hosts.yml`, `configure.yml` or `teardown.yml`.

## User workflow

1. Add a Git source, including its credential reference for a private repository. Browse the scenario catalog or open a native scenario by repository, branch and relative folder path.
2. Fork a public source or select a writable branch. Edit scenario files and shared dependencies in Config, then save. Git preserves files that were not loaded into the browser.
3. Select Deploy, an existing Range42 environment, feature flags and optional non-secret parameters. Creation pins the complete repository commit. Environment selection determines the Proxmox target and credentials.
4. Review deployment preflight, then run the full workflow. Follow its attempt logs and use Cancel when needed.
5. Use the saved scenario's available lifecycle actions. Destructive actions require the deployment record's codename. Every action keeps the original commit and feature selections; create a deployment from a newly saved revision to change them.

The deployment record's codename identifies its API history. The selected context's codename and scenario remain the native environment identity. Native scenarios use their saved topology; `team_count` must be 1. Team replication must be authored into the native scenario itself.

## Backend on the deployer-cli

Run this API as the existing deployment user on the deployer-cli, where `range42-context` already works. An API installed on another VM does not gain access to those contexts merely by registering a Proxmox host. Register this backend's authenticated URL in the UI. The [example systemd service](../examples/native-deployer-api.service) is a local installation template, not an installer and not an enabled service.

Required local executables are zsh, Ansible, OpenSSH, jq and yq. The API image and CI install the shell tools. The deployment user must also have the normal Range42 core, SDN controller, devkit and catalog runtime, its catalog-specific `.env` configuration, trusted SSH hosts, and initialized vault/key material. Python requirements alone do not install these repositories.

Set `RANGE42_CONTEXT_ROOT` to the existing context directory and `RANGE42_CONTEXT_SCRIPT` to the installed native `range42-context.sh`. With neither configured, discovery uses the deployment user's `~/range42.config` and the core location recorded in each context's `sourced_range42.sh`. Discovery reads literal values without sourcing the shell files.

For explicit labels, a limited context list or custom template inputs, set `RANGE42_NATIVE_CONTEXTS_FILE` to a private, operator-owned JSON file:

```json
{
  "version": 1,
  "contexts": [{
    "id": "training",
    "label": "Training environment",
    "workspace": "/home/deployer/range42.config/TRAINING-demo_lab",
    "context_script": "/home/deployer/range42/range42/roles/deployer.bootstrap/files/range42-context.sh",
    "inventory_variables": {}
  }]
}
```

The workspace directory must match its exported `codename-scenario`. It must contain `sourced_range42.sh`, `inventory/inventory_default.yml`, `secrets/default_vault.yml` and `secrets/vault_pass.txt`. Register exactly one Proxmox host whose API hostname matches the context inventory. `GET /v1/contexts` reports missing prerequisites without returning vault contents or private filesystem paths. It also rejects the older generated-scenario CLI adapter that treats every `main.yml` as requiring `hosts.yml`.

Custom SSH key locations can be supplied through `inventory_variables` using the native `DEPLOYER_CLI__DST_SSH_KEYS_*_DEST_DIR` template variables. Otherwise the standard deployment-user `.ssh/range42/<codename-scenario>/` layout is used.

Native execution calls the installed `range42-context use` followed by its lifecycle command. This performs the normal user-level context switch, including SSH configuration and devkit secret links. Use one API instance and its provisioning lock per deployment user; do not run an independent CLI context switch during an attempt. The API creates a dedicated SSH agent, persists its ownership for restart recovery, and cleans it up on exit.

After selection, an attempt-local copy preserves the saved repository's entire relative layout. Its selected scenario receives context-owned secret/key links. The saved inventory and SSH templates render into the attempt directory; saved repository bundles override the ambient bundle location. The original scenario link, inventory and Git checkout are not rewritten by this execution view. Native parameters reach every `ansible-playbook` invocation, including older wrappers that do not forward arguments.

A container deployment needs the same native paths, deployment-user home and writable context-switch files visible at their original locations. The standard generated-project container profile alone is insufficient. The deployer-cli service is the straightforward option.

## Repository contract

Each native scenario has a nonempty Ansible playbook list in `main.yml` or `main.yaml`, `manifest/scenario_vms.json` with a `vms` array of positive integer `vm_id` values, and `templates/ansible-inventory.j2`. An empty VM array is allowed for diagnostic workflows. `templates/ssh-config.j2` and `manifest/feature_flags.yml` are optional. Feature flags use declared boolean choices and execute as `INSTALL_<ID>=YES|NO`.

| API action | Native command | Conventional script suffix |
| --- | --- | --- |
| `full` | `deploy` | `.setup.sh` |
| `configure` | `deploy` through an attempt-local configure shim | `.configure.sh` |
| `deploy_vms` | `deploy-vms` | `.setup_vms_only.sh` |
| `teardown` | `delete` | `.delete_all.sh` |
| `delete_vms` | `delete-vms` | `.delete_vms_only.sh` |
| `reset` | `reset` | `.reset.setup.sh` |
| `deploy_networks` | `networks-apply` | `.setup_networks.sh` |
| `delete_networks` | `networks-delete-sdn` | `.delete_networks.sh` |

A unique suffix match handles scripts whose prefix differs from the folder name. A playbook fallback handles full/configure/teardown when provided. Ambiguous names or custom bundle roots can be declared in `manifest/scenario_runtime.json`:

```json
{"version":1,"actions":{"full":"install.sh","teardown":"remove.yml"},"bundle_path":"shared/bundles"}
```

Action paths are relative to the scenario; the bundle path is relative to the project repository root. Paths cannot escape that root. Only discovered actions are offered. No teardown or configure action is invented for a scenario that lacks one.

## Validation and remaining acceptance

The local compatibility matrix uses playbooks commit `6dcf31b5b53600f41be1ce7553d489dba4ad803b`. All 18 scenarios pass native discovery, wrapper shell syntax, and inventory/SSH template rendering with local fixture values. See [the recorded matrix](native-scenario-matrix.json). API tests cover context selection, immutable checkout, preview, creation, preflight and attempt startup. Local real-Ansible tests cover relative shared imports, context secrets and feature propagation. These checks do not deploy any infrastructure.

Local verification on 2026-09-23: the full API suite passed 1,542 tests (8 environment-dependent skips); the final focused native, catalog, trigger and runner checks also passed after the last fixes. The UI suite passed 1,919 tests (10 skips), with type checking, ESLint and a production build. Ruff and OpenAPI drift checks passed. Disposable SQLite checks verified migration upgrade and guarded downgrade. Process-level cancellation tests verified quiet and nested commands both through the live handle and saved runner identity. Vite retains its existing large-chunk warning; no browser-to-live-infrastructure acceptance is claimed.

Existing VMID protections still apply. The default `blank_scenario_4_subnets` manifest conflicts with 4001–4004; `kunai_lab` conflicts with 1111. Customize those IDs consistently in a fork before using a target with those protections. Native diagnostic targets declared through literal `set_fact` VM parameters and explicit VMID parameters are checked too. `debug_sdn_tests` defaults to existing VM 102 and recreates its NIC and test SDN zone despite declaring zero VMs; select disposable resources explicitly.

Native playbooks are executable deployment code. Preflight identifies the selected context and known VM targets, then reports that arbitrary native stages can affect additional resources. Generated-project capacity, allocation ownership and runtime snapshot controls do not describe native workflows. External controller/catalog readiness, service-specific configuration and the effects of arbitrary custom scripts require scenario acceptance in the selected environment.

Live acceptance remains outstanding: choose an operator-designated disposable context, save a scenario-specific customization, run preflight/full deployment, verify its services and SDN, run only its declared cleanup action, then verify cleanup and protected resources. Repeat per scenario and record the context, full source/runtime revisions and attempt IDs. No existing management context has been designated disposable or used for this work.
