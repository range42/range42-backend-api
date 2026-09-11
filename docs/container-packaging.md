# Backend container packaging

The image runs one non-root API process on Python 3.12, matching the verified shared deployment runtime. CI also exercises Python 3.13 separately. Startup validates the bearer token and credential encryption key before initializing state, runs the packaged Alembic migrations, and then executes Uvicorn. Credentials are never generated or rotated on startup.

The default Compose service binds `127.0.0.1:8000`. Set `RANGE42_LISTEN_ADDRESS` and `RANGE42_CONTAINER_PORT` deliberately for a different listener, and put TLS/reverse proxy access in front of a remotely exposed installation. Set `RANGE42_CORS_ORIGINS` for the UI's actual origin. Use one API instance per state volume; do not scale multiple containers against the same SQLite workspace.

## First start

Use Docker Engine and Docker Compose with support for long-form bind mounts and `create_host_path: false`. Prepare private credentials outside the repository; the following writes two new files and refuses to replace existing files:

```sh
export RANGE42_CONTAINER_SECRETS_DIR="/absolute/private/path/range42-container-secrets"
export RANGE42_CONTAINER_UID="$(id -u)"
export RANGE42_CONTAINER_GID="$(id -g)"
python3 - <<'PY'
import base64, os, secrets
from pathlib import Path
os.umask(0o077)
root = Path(os.environ['RANGE42_CONTAINER_SECRETS_DIR'])
root.mkdir(mode=0o700, parents=True, exist_ok=True)
for name, value in [('api-token', secrets.token_hex(32)),
                    ('credential-key', base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())]:
    with (root / name).open('x') as stream:
        stream.write(value + '\n')
PY
docker compose build
docker compose up -d
```

The build UID/GID must match the owner of the secret files. For a prebuilt image, provision files readable by its configured non-root UID instead. File-backed [Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/) are read-only bind mounts; Compose does not remap their host ownership using `uid`/`gid` settings. Keep secret directories private and files mode 0600. Do not mount your entire `~/.ssh`, include credentials in an image, or put the token itself into Compose environment values. Supply the API token to the UI through its backend connection settings.

The image build context is an allowlist of application code, playbooks, requirements and migration files. Host inventory, local workspaces, Git metadata, `.env` files and secret directories are excluded. The supplied requirements retain their existing version policy; an image rebuild may resolve newer transitive Python/Ansible dependencies. Promote an immutable image digest after validation rather than treating the local tag as a reproducible release lock.

## Migrating the earlier container installer

The current Hyde `bundles/admin/software.install.deployer_api_backend` installer renders the older `PORT`, `UID`, `GID`, `IMAGE_NAME` and `SSH_KEY_PATH` variables, `/home/range42` bind mounts, an after-start migration command, and an unauthenticated OpenAPI probe. It does not provision the new bearer/encryption-key secrets or complete runtime profile. That installer needs a separate coordinated update before it can deploy this packaging contract.

For manual migration, translate the old listener/build/image settings to `RANGE42_CONTAINER_PORT`, `RANGE42_CONTAINER_UID`, `RANGE42_CONTAINER_GID` and `RANGE42_CONTAINER_IMAGE`. Set actual allowed UI origins with `RANGE42_CORS_ORIGINS`. The old `.env` is no longer loaded wholesale into the service, and `SSH_KEY_PATH` has no replacement whole-home mount: use the private workspace credential template instead. Review any existing `docker-compose.override.yml` explicitly; Compose loads that file automatically unless you specify compose files with `-f`.

Before starting, stop only after attempts are terminal, back up existing state and credentials, and migrate the complete old workspace tree/database into the selected persistent mount. Preserve paths needed by historical attempt records; the defaults do not automatically relocate absolute paths from an old database. Use an explicit reviewed Compose override for the original persistent workspace/DB paths when necessary. Never start against a new empty named volume and assume it imported the old database. After validating secrets, migrations now run before API startup, and the installer probe must use `/v1/health`; authenticated readiness remains a separate check.

## Persistent state and health

Compose creates a named `state` volume mounted at `/var/lib/range42`. It contains:

| Path | Purpose |
| --- | --- |
| `workspaces/.range42.db` | SQLite state and Alembic revision, with WAL sidecars while running |
| `workspaces/<codename>-<scenario>/` | Inventories, private credentials, project checkouts, runner artifacts and replay state |
| `home/.ssh/range42/` | Writable SSH ControlMaster files |
| `home/.ansible/` | Writable Ansible local state |

New private directories use mode 0700 and database files use mode 0600. The image's application code is root-owned and Compose makes its root filesystem read-only. `/tmp` is a bounded tmpfs. If replacing the named volume with a bind mount, prepare ownership for the image UID and use a supported local Linux filesystem. Keep database and workspace paths persistent together. Never point two deployments at the same live volume.

`docker compose down` retains this named volume. **`docker compose down --volumes` deletes it.** Back up the stopped/idle state volume and both secret files together using a consistent SQLite backup or quiesced filesystem copy. Losing the original encryption key prevents decrypting stored credentials. Restoring just the database does not restore execution artifacts or project workspaces.

Docker's health check calls the public, non-sensitive `GET /v1/health`. Protected endpoints, including OpenAPI and `GET /v1/health/ready`, require `Authorization: Bearer <token>`. Readiness reports `ready` in JSON; check that boolean as well as HTTP status. It verifies writable workspaces, SQLite WAL and successful access to every registered Proxmox host. It does not certify that an SDN runtime or a deployment-specific vault is installed. A new API can support Git/catalog onboarding before the optional execution runtime is configured.

## Optional immutable SDN execution runtime

Start with a reviewed, matched export of playbooks, controller roles, catalog, core roles and pinned Ansible collections. Do not merge unfinished upstream work as part of container startup. The optional `docker-compose.runtime.yml` mounts one complete runtime export at `/runtime` and one private workspace credential template at `/run/range42-template`, both read-only with host auto-creation disabled.

Set `RANGE42_RUNTIME_DIR` to an absolute directory containing:

```text
range42-playbooks/                         # bundles/, scenarios/, .range42-revision
range42-ansible_roles-proxmox_controller/   # roles/, .range42-revision
range42-catalog/                           # layer roles, CTF content, .range42-revision
range42/                                   # roles/, .range42-revision
collections/                               # reviewed pinned Ansible collections
ansible.cfg                                # reviewed Ansible settings, host-key checking enabled
proxmox-ca.pem                             # combined public roots + verified Proxmox CA
bundle-runtime.json                        # generated explicitly below
```

`proxmox-ca.pem` must include the public trust roots needed by your playbooks as well as the verified Proxmox CA, and the configured API URL must match the certificate hostname or SAN. The runner uses this file for `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE` globally; a file containing only a private PVE CA can break HTTPS tasks against public services.

Set `RANGE42_WORKSPACE_TEMPLATE_DIR` to a private template with `secrets/default_vault.yml` and `secrets/vault_pass.txt`, plus the optional `ssh_keys/backend_keys`, `jump_keys`, `student_keys` and verified `ssh_keys/known_hosts` files. Follow the existing workspace template contract: real directories mode 0700, regular files mode 0600, no symlinks, coherent vault/key passphrases, and ownership readable by the container UID. These credentials are copied privately into each new deployment workspace. Existing workspaces retain their credentials. See [backend security](backend-security.md) for the SSH trust contract. The template is separate from the API token/encryption-key secrets.

Generate the runtime profile once under the **container's** paths and exact environment. A profile generated for `/opt/range42` host paths cannot be reused as `/runtime`. This command overrides the entrypoint deliberately to fingerprint the installed read-only components without starting the API or migrating state:

```sh
docker compose -f docker-compose.yml -f docker-compose.runtime.yml run \
  --rm --no-deps --entrypoint python api -c '
import json
from pathlib import Path
from app.core.bundle_runtime import build_runtime_manifest
components = {
    "playbooks": Path("/runtime/range42-playbooks"),
    "controller": Path("/runtime/range42-ansible_roles-proxmox_controller"),
    "catalog": Path("/runtime/range42-catalog"),
    "range42": Path("/runtime/range42"),
    "collections": Path("/runtime/collections"),
    "ansible_config": Path("/runtime/ansible.cfg"),
}
print(json.dumps(build_runtime_manifest(components), indent=2))
' > "$RANGE42_RUNTIME_DIR/bundle-runtime.json"
docker compose -f docker-compose.yml -f docker-compose.runtime.yml up -d
```

Require successful profile generation before starting. The profile records component revisions and hashes and runtime checks reject drift. Do not regenerate it on each restart to bless changed code. Mounts prevent container writes; operators must also refrain from changing host export files during active attempts. The read-only runtime can still be replaced by its host owner, so a bind mount alone is not an atomic release mechanism. See [bundle provenance](bundle-attachments.md) for dependency coverage and limits.

## Restart and upgrade limits

Drain all attempts to terminal state before stopping, recreating or upgrading this container. [Docker stops the container's main process and ultimately terminates its process namespace](https://docs.docker.com/reference/cli/docker/container/stop/); detached Ansible and SSH-agent processes cannot survive container removal. The verified systemd API-only restart recovery does not imply container-recreation recovery. The 60-second stop grace period permits API shutdown and database cleanup; it is not a guarantee that a long deployment finishes. Preserve state for diagnosis after an unexpected container termination and verify actual owned resources before retrying.

The image also enables a persistent HTTP admission lock. A protocol-aware installer can hold the [maintenance drain guard](container-maintenance.md) through controlled replacement, covering finite legacy requests and durable detached attempts together. Merely observing terminal rows without holding this guard does not close the race with a new request.

SSH-agent sockets must fit Linux's Unix-domain socket pathname bound: the full encoded socket pathname must be shorter than 108 bytes. With the default workspace root and current `.agent-<8-character>/s` suffix, the combined `<codename>-<scenario>` directory name must be at most 61 ASCII bytes; non-ASCII names consume more bytes. Longer paths fail explicitly rather than placing a socket in transient `/tmp`. Choose short names or a shorter persistent workspace mount when provisioning a deployment.

## Repeatable packaging acceptance

Build a locally unique image and opt into the disposable Compose smoke:

```sh
docker build --target runtime -t range42-backend-api:packaging-test .
RANGE42_CONTAINER_TEST_IMAGE=range42-backend-api:packaging-test \
  python -m pytest tests/test_container_packaging.py tests/test_container_smoke.py
```

The smoke creates unique projects, ports, volumes and synthetic secrets; it never contacts Proxmox or clones a repository. It checks public liveness, unauthenticated rejection, authenticated readiness, migration head, encrypted catalog credentials, writable private SSH state and persistence across container recreation. A second run adds fixture runtime mounts, generates and verifies a profile under actual container paths, and confirms that runtime writes fail with a read-only filesystem error. It then removes only its disposable resources. Existing Docker containers and volumes are untouched. This verifies packaging, not real SDN execution; use the reviewed matching release's deployment acceptance separately.
