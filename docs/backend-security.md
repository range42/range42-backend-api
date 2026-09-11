# Backend access and credential configuration

The API requires bearer authentication by default. Configure the browser's backend connection with the API token. HTTP routes, SSE streams, readiness and API documentation require `Authorization: Bearer <token>`; only `GET /v1/health` is public. Browser streaming must use authenticated fetch rather than query-string credentials. The legacy WebSocket handshake also requires the header and is not compatible with browser WebSocket clients that cannot set it.

| Variable | Contract |
| --- | --- |
| `RANGE42_AUTH_MODE` | `required` by default. Set `development` explicitly only for a local development instance without authentication. |
| `RANGE42_API_TOKEN_FILE` | Preferred token source. Readable file containing at least 32 non-whitespace characters. |
| `RANGE42_API_TOKEN` | Alternative token source; cannot be combined with the file setting. |
| `RANGE42_CREDENTIAL_KEY_FILE` | Preferred persistent Fernet encryption-key file. Back up separately from the database and retain across upgrades. |
| `RANGE42_CREDENTIAL_KEY` | Alternative Fernet key; cannot be combined with the file setting. |
| `RANGE42_CORS_ORIGINS` | Comma-separated exact browser origins, including scheme and nonstandard port. Loopback development origins remain allowed by the default regex. |
| `CORS_ORIGIN_REGEX` | Optional operator override; set to an empty value to disable regex matching and allow only explicit origins. |
| `RANGE42_GIT_ALLOWED_HOSTS` | Exact Git authorities, default `github.com,gitlab.com,codeberg.org`. Include every self-hosted forge as `hostname` or `hostname:port`. No wildcards. This setting replaces the default list. |
| `RANGE42_GIT_ALLOW_HTTP` | Disabled by default. Set `1` only when an explicitly allowed forge requires plain HTTP. HTTPS is preferred. |
| `RANGE42_PROXMOX_CA_FILE` | PEM trust bundle for the registered Proxmox endpoints. The URL hostname/IP must match the certificate SAN. Without this variable HTTPX uses normal trusted roots. Certificate verification is never disabled. |

Store token/key files outside the database and repository, readable only by the backend service account. Required authentication mode fails at startup when token or encryption-key configuration is missing or invalid. Development mode still requires an encryption key before storing Git or Proxmox credentials.

New `sources.token_ref` and `proxmox_hosts.token_ref` values are encrypted using authenticated Fernet encryption. ORM consumers receive plaintext only in process memory. Startup migrates existing plaintext values in one transaction and verifies that the configured key can decrypt existing ciphertext before serving. Run Alembic migrations before starting the service. SQLite accepts the expanded encrypted values in the existing token columns, so this conversion does not introduce a schema revision.

Old backups and SQLite free pages/WAL may retain previous plaintext values: rotate legacy credentials and handle previous database copies accordingly. Encryption of database fields does not encrypt runner inputs, Ansible vault passwords, process memory, or remote forge storage. Execution artifacts remain protected by the workspace permissions and runner lifecycle. Losing the encryption key makes stored credentials unusable; this release requires an offline re-encryption procedure for key rotation.

Git URL validation runs at source/project ingress and again before catalog, legacy compose and concrete scenario network fetches. Embedded credentials, unexpected schemes, unapproved hosts/ports, queries and fragments are rejected. Redirects are disabled for clone/fetch, so configure the canonical forge URL directly. Operators must trust the DNS and service behind explicitly approved hosts. Personal access tokens use the separate credential fields.

The bearer token is a shared operator credential. This provides an application access boundary; user accounts, per-project authorization and individual operator attribution are not implemented by this change. Serve the UI and API behind TLS or through the authenticated lab network, and keep the token out of public UI configuration.

Implementation references: [HTTPX certificate verification](https://www.python-httpx.org/advanced/ssl/) and [cryptography Fernet](https://cryptography.io/en/latest/fernet/).

Concrete deployments enable Ansible host-key checking and use the inventory's
workspace `ssh_keys/known_hosts` for the Proxmox node and guest connections.
The connection-reuse arguments never force `StrictHostKeyChecking=no` or
discard keys through `/dev/null`. Generated inventories use `accept-new`:
unknown keys are recorded on first connection, and a changed saved key stops
the connection. This is trust on first use; initial keys are independently
verified only when an operator supplies a verified known-hosts template.
Do not remove a changed key to bypass the failure; establish why it changed
and verify its replacement first. Legacy inventories retain their own host
configuration. See [OpenSSH host-key policy and option precedence](https://man.openbsd.org/ssh_config#StrictHostKeyChecking)
and [Ansible SSH connection settings](https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/ssh_connection.html).
