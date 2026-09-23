# Concrete project: content demo

This runnable example keeps its content, shell script, inventory and Ansible
playbooks in one Git revision. It copies a file, processes that file with the
script, imports `configure.yml`, verifies the output, and writes a JSON result.
All tasks execute on the machine running Ansible. They write only beneath
`r42_workspace_dir/demo-output`; direct CLI runs default to
`/tmp/range42-content-demo/demo-output`.

The scenario contains:

```text
scenarios/content_demo/
  main.yml
  configure.yml
  hosts.yml
  manifest/scenario_vms.json
  files/message.txt
  scripts/process.sh
```

Run it from this directory with an environment containing `ansible-playbook`:

```sh
ansible-playbook -i scenarios/content_demo/hosts.yml scenarios/content_demo/main.yml
cat /tmp/range42-content-demo/demo-output/orchestration.json
```

Expected result: `{"scenario": "content_demo", "content_verified": true}`. Run the
same command again to reapply the content and script. `configure.yml` can also be
run directly after `main.yml` has produced the processed file.

## Register a pinned Git project with the API

Commit the example to a repository the backend can clone. Copying this directory
to the repository root gives a dedicated project. Keeping it inside another
repository uses a shared repository subdirectory, such as
`examples/concrete-project`. Obtain the full commit SHA containing **all** files;
a branch name is not a deployment pin. The backend checks out that revision for
the attempt and resolves `scenarios/content_demo/main.yml` beneath the project's
configured subdirectory. `topology.json` is not required for this concrete
scenario.

The following requests are templates. Replace the repository owner/name, IDs
and commit SHA with your values. Send them to your selected backend or gateway;
include its usual bearer authentication when configured.

1. Register the Git source with `POST /v1/catalog/sources`:

   ```json
   {
     "provider": "github",
     "base_url": "https://github.com",
     "auth_kind": "none",
     "repos": [{"owner": "your-account", "repo": "your-scenarios", "branch": "main"}]
   }
   ```

   Save the response's `id`. Public repositories need no token. Private sources
   use `auth_kind: "pat"` and `token_ref` containing a backend-stored access token.
   Catalog refresh is separate from checking out an executable project.

2. Register the project with `POST /v1/projects/`:

   ```json
   {
     "name": "Local content demo",
     "source_id": "REPLACE_SOURCE_ID",
     "branch_strategy": "shared_repo_subdir",
     "repo_owner": "your-account",
     "repo_name": "your-scenarios",
     "subdir": "examples/concrete-project"
   }
   ```

   For the example copied to a repository root, use
   `"branch_strategy": "dedicated_repo"` and `"subdir": ""` instead.

3. Create a deployment with `POST /v1/deployments/`:

   ```json
   {
     "codename": "CONTENT_DEMO",
     "scenario_label": "content_demo",
     "project_id": "REPLACE_PROJECT_ID",
     "target_host_id": "REPLACE_REGISTERED_HOST_ID",
     "team_count": 1,
     "project_sha": "REPLACE_WITH_FULL_COMMIT_SHA"
   }
   ```

   The current API requires a registered Proxmox target even for this localhost
   example. Use an existing target from `GET /v1/proxmox/hosts`; the response is a
   page with an `items` array. Normal deployment workspace/credential setup and
   the target's API readiness check still apply. The example playbooks themselves
   contain no Proxmox calls, VM creation or network changes.

4. Run `POST /v1/deployments/REPLACE_DEPLOYMENT_ID/preflight` with no body. Inspect
   its checks and resolve any blocking result. Start the attempt with
   `POST /v1/deployments/REPLACE_DEPLOYMENT_ID/attempts` and this body:

   ```json
   {"scope": "full"}
   ```

   Automatic start uses the backend's default `RANGE42_AUTO_START_ATTEMPTS=1`.
   Inspect `GET /v1/deployments/REPLACE_DEPLOYMENT_ID/attempts` and the deployment
   event stream for the task results. The three output files live under the
   deployment's returned `workspace_path` in `demo-output/`.

## Extending this into an infrastructure scenario

SDN is the intended default for new infrastructure setups. Its preparation stage
belongs before VM bootstrap, readiness checks and content configuration. There
is no SDN placeholder task pretending to provision a network in this local
example: it is a content execution sample, not an SDN deployment.

When the Hyde SDN integration is installed and validated, an infrastructure
scenario should import the supported `proxmox/sdn_network.bootstrap` bundle
before VM bootstrap. Its inputs are `BUNDLE_SDN_ZONE` and `BUNDLE_SDN_VNETS`.
Keep those concrete imports and their variables in the same reviewed scenario
tree, and declare actual VM IDs in `manifest/scenario_vms.json`. Existing bridges
remain an explicit compatibility choice.

Actual Range42 bundle execution also needs the backend environment's
`RANGE42_BUNDLE_DIR` pointing to the installed `range42-playbooks/bundles`
directory, with the required roles available to Ansible. The backend sets
`RANGE42_ACTIVE_CONFIG_DIR` to the deployment workspace; bundle vault imports
therefore expect `secrets/default_vault.yml` beneath that workspace, plus the
appropriate SSH keys and vault password setup. Keep those runtime credentials
out of the project repository. This local content example needs none of those
bundle dependencies.

The sample's actual runner execution is checked by
`tests/integration/test_concrete_runner_local.py` in the backend repository.
