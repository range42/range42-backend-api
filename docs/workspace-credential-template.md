# Operator defaults for new deployment workspaces

Set `RANGE42_WORKSPACE_TEMPLATE_DIR` to a private operator-managed directory to
let new UI/API deployments inherit the installation's default vault and SSH
credentials. This directory is runtime configuration, outside every Git checkout.
The setting is optional; without it, workspace creation keeps its existing
behavior.

```text
/etc/range42/workspace-template/
  secrets/default_vault.yml
  secrets/vault_pass.txt
  ssh_keys/known_hosts
  ssh_keys/backend_keys/...
  ssh_keys/jump_keys/...
  ssh_keys/student_keys/...
```

The vault and password are required together. SSH files are optional. Only the
listed locations are copied. Keep directories mode 700 and every selected file
mode 600, including public keys and `known_hosts`. Symbolic links, special files,
unsafe permissions and files over 1 MiB are rejected before a workspace is
changed. A template can contain at most 256 selected files. Use a dedicated vault
containing only deployment variables and key passphrases, not unrelated
infrastructure service credentials.

Credentials are inherited as one set. Existing workspace credentials are left
untouched and prevent all default inheritance; the backend never combines a
user's password with a vault encrypted using another password. An API create
request carrying explicit `secrets` also skips the template and retains the
existing caller-managed credential flow. Omitting `secrets` opts into configured
installation defaults. Changing the template affects future workspaces only.

New workspaces/directories are private, and copied files use mode 600. Copy
failures remove only files and empty directories created by that operation.
Misconfigured templates return `WORKSPACE_TEMPLATE_INVALID`; no template data
is included in the API response. Protect and back up the source template and
existing workspaces separately from application releases.

This is an installation-wide default for operators who trust users of that
backend with the same target credentials. It does not provide per-user secret
isolation or a browser credential-upload interface. Separate installations or
explicit workspace credentials remain appropriate when targets require different
access. Use the authenticated API host registry for the selected Proxmox token;
concrete runtime target variables override obsolete vault target values.
