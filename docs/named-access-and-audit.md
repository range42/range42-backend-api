# Named backend access and mutation audit

The backend can authenticate named bearer tokens as **admin**, **operator** or **viewer**. Roles apply across one installation. They do not isolate users' projects, repositories, deployments or teams from other users with the same role. Git provider permissions remain independent.

- **Viewer** can browse catalog/project/deployment metadata, status and redacted deployment events, and read basic infrastructure inventory. Raw guest configuration and mutations are restricted.
- **Operator** additionally authors backend project records and runs reviewed deployment, reservation, guest configuration, lifecycle and snapshot workflows. Registration of hosts, catalog credentials and global administration require admin access.
- **Admin** retains all existing routes, including legacy administration.

The role policy names individual registered route templates. A new route is denied to non-admin roles until explicitly reviewed. Both HTTP and WebSocket requests pass through authentication and authorization before handler execution. The public liveness probe remains available.

## Operator configuration

`RANGE42_API_PRINCIPALS_FILE` names a private JSON file (mode0600 or0400), at most256KiB and1000 principals. Its shape is:

```json
{
  "version": 1,
  "principals": [
    { "id": "alice", "role": "admin", "token_sha256": "<64 lowercase hexadecimal SHA256 characters>" },
    { "id": "bob", "role": "operator", "token_sha256": "<a different token hash>" }
  ]
}
```

Generate independent high-entropy bearer tokens (for example `secrets.token_urlsafe(32)` using Python) and compute SHA256 over their exact UTF-8 bytes. Distribute each token privately. The file stores hashes, never plaintext tokens; these are API credentials, not user passwords. The backend refuses duplicate IDs/hashes, invalid roles, unknown fields, publicly readable files and configurations with no administrator. The file is read at startup: rotate or revoke by replacing its entry and restarting the backend. Existing browser sessions retain their own supplied token until changed; revoked tokens stop authenticating after restart.

The existing `RANGE42_API_TOKEN` or `RANGE42_API_TOKEN_FILE` remains compatible and is attributed as `shared-operator` with admin access. Remove that configuration when migrating fully to named credentials. Do not attribute a shared token to an individual. The browser's local display name only labels Git collaboration; it does not grant backend permissions.

In Settings, **Check backend access** reads the active token's backend identity. Administrators can page through the mutation audit. Changing backend or token clears the displayed identity and audit and rejects late responses from the previous connection. Existing action dialogs may still render a control the current role cannot use; the backend enforces the role and returns403 before execution.

## Durable request audit

Migration`0008_audit_records` creates the journal after snapshot migration0007. Named-principal mode always enables mutation auditing. Existing single-token installations can enable it with `RANGE42_AUDIT_ENABLED=true` after migration. No live identity configuration is changed merely by installing this feature.

Every authenticated HTTP mutation, including a role-denied mutation, records an intent before route execution. If the audit store is unavailable, the handler does not run and the response is503`AUDIT_UNAVAILABLE`. A response records the HTTP status and completion; if recording that result fails, the durable intent remains unfinished and the response preserves the actual action result with `X-Range42-Audit-State: unconfirmed`. Clients must investigate rather than repeat an already accepted action. `X-Range42-Audit-Id` identifies the journal record.

The journal includes actor, role, method, route template and timestamps. It excludes request bodies, concrete URL parameters, queries, tokens, response bodies and arbitrary error details. It records HTTP request outcomes, not guest task completion, rollback proof or exact edited values. Native task and deployment journals remain the execution evidence. Unfinished intents survive crashes and need investigation; there is no automatic replay.

`GET /v1/auth/me` returns identity, installation scope and audit status. `GET /v1/admin/audit?offset=0&limit=50` returns newest-first records (limit1–200); it is admin-only. New writes may shift offset pages, so the UI deduplicates IDs and provides refresh. The backend provides no audit deletion endpoint or automatic expiry. A populated journal blocks schema downgrade to avoid silently discarding evidence; storage retention/export is an operator responsibility.

This implements coarse installation roles and an attributed request journal. Per-project ownership, SSO, per-user quotas, automatic audit archival, continuous token-file reload and audit of out-of-band Proxmox/Git operations remain separate capabilities.

The policy follows [OWASP authorization guidance](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html) on default denial and per-request enforcement, and [OWASP logging guidance](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html) on useful attribution without recording secrets.
