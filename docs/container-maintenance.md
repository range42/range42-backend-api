# Container maintenance admission and drain contract

Set `RANGE42_MAINTENANCE_LOCK_FILE` to a persistent absolute local path owned by the API user. The container image sets `/var/lib/range42/maintenance.lock`. The parent directory must be owned by that user and not writable by other users; the regular lock file is mode 0600, with no symbolic links. Keep it in persistent state and never replace it while processes are running. Unconfigured installations continue serving normally and explicitly report that this maintenance capability is disabled.

Each finite HTTP handler acquires its own shared flock descriptor before entering the route. The downstream ASGI task owns that descriptor until the handler, response and FastAPI/Starlette background tasks finish. Caller disconnect or repeated cancellation cannot unlock a real sync/threadpool job that is still running. Shutdown drains these tracked jobs before disposing database state.

Only exact `GET /v1/health` and `GET /v1/deployments/<single-path-segment>/events` bypass the admission lock. Downloads, trailing-slash variants, HEAD, OPTIONS and other methods do not bypass it. CORS middleware may answer a preflight itself, without executing a route. Client `Accept` headers never authorize bypass. The existing status WebSocket is read-only and does not launch operations. A future streaming or background execution endpoint must be reviewed against this contract before release.

An exclusive flock rejects new finite requests with HTTP 503 and `Retry-After: 5`, including legacy `/v0` runners and direct `/v1/proxmox` operations. A replaced, public, foreign-owned or otherwise unverifiable gate file also fails closed. Public liveness remains minimal and does not prove that the maintenance gate is valid.

## Authenticated capability proof

`GET /v1/admin/maintenance` requires the configured bearer token and returns:

```json
{
  "protocol": "flock-http-v1",
  "enabled": true,
  "process": {"pid": 2, "start_time": "123456", "boot_id": "host-boot-id"},
  "lock": {"path": "/var/lib/range42/maintenance.lock", "device": 1, "inode": 42, "uid": 1000}
}
```

The values above are illustrative. No credentials appear in the response. A disabled gate returns `enabled: false` and `lock: null`. An installer must obtain the proof from the actual running API, then pass it to the helper inside that same container's PID namespace. Inspect the immutable container ID and configured state mounts before acting; a similarly named replacement container is not the original target.

## Held helper protocol

Run `python -m app.core.maintenance_guard` inside the target container with stdin/stdout pipes controlled by the installer. Send one JSON proof line and **keep stdin open**. The helper:

1. Verifies the protocol, current API PID/boot/start identity, gate inode/path and ownership against the authenticated proof.
2. Attempts the exclusive admission flock. Busy finite handlers cause a refusal, allowing the installer to retry after they complete.
3. Acquires the installation's provisioning flock and a SQLite `BEGIN IMMEDIATE` transaction without writing data.
4. Rejects nonterminal/unknown attempts, any workspace lock, malformed or inconsistent artifact ownership, unindexed artifacts without verifiable identities, and any verified live detached runner. It checks both recorded attempts and all workspace runner directories, including cancelled attempts whose processes remain alive.
5. Rechecks the API/gate identity and emits one `{"status":"idle",...}` acknowledgement while retaining all three locks.

The installer may stop the **same immutable container ID** only after that acknowledgement and while the helper is still alive. New raw HTTP work cannot enter through the admission gate, and new durable reservations cannot commit through the SQLite writer lock. Existing detached work is excluded by the provisioning/attempt/artifact audit. Keep the helper alive until the controlled stop terminates the old container. EOF, installer failure or explicit release closes the helper and releases its locks without changing the database. Do not use a one-shot pipe such as `echo proof | helper`: EOF would release the gate before container replacement.

Refusal emits a safe machine-readable response and a nonzero exit status. It must not trigger stop, source synchronization over the installed runtime, credential replacement or database migration. Malformed or unknown state requires investigation; the helper does not reinterpret it as idle. The checks coordinate this API installation; they do not lock external Proxmox writers or arbitrarily authored remote background services. Drained backend work also does not prove cluster-wide Proxmox task idleness: a raw route can return an asynchronous UPID while its remote Proxmox task continues independently. Such a task survives API stop and requires its own task-status verification when the operation calls for it.

## Installer behavior and compatibility

A managed installation whose image, configuration, runtime profile, state bindings and credentials are already unchanged should verify readiness and return a no-op. An actual update uses the held helper before controlled replacement. A stopped installation may be updated after verifying its configured persistent paths, original credentials and artifacts offline. Unknown legacy containers without this protocol require an explicit offline migration and remain untouched by the automatic running-update path.

Container removal still terminates detached processes. This gate prevents a controlled update from removing a busy container; it does not make an unexpected Docker/container crash recoverable in the same way as an API-only systemd restart. Keep the original database, workspaces, encryption key and token throughout migration. See [container packaging](container-packaging.md) for mounts, credentials and restore limits.

## Verification scope

Unit/HTTP checks cover exclusive-lock rejection, exact safe route bypass, real slow sync and background jobs under repeated caller cancellation, error cleanup, new-process behavior under a held persistent gate, changed identities, unsafe files, held SQLite/provisioning locks, active/unknown state and real live runners.

The opt-in `tests/test_container_smoke.py` uses a uniquely named disposable Compose project. It obtains the real authenticated capability, keeps the container-local helper alive, verifies legacy and v1 mutations return 503, then recreates the idle container and verifies the gate inode persists while API process identity changes. The same checks run with the immutable runtime fixture mounted read-only. These Docker checks are skipped in a normal Python suite unless `RANGE42_CONTAINER_TEST_IMAGE` is supplied; report their separate result explicitly.
