# Git credential references

Catalog/source credentials may remain literal PATs or use an explicit `env://NAME` or `file:///absolute/path` reference in the existing `token_ref` field. This applies to catalog listing/detail/refresh, pinned bundle resolution, and project checkout for preflight and deployment. Proxmox credentials and the database encryption key retain their existing contracts. No secret-reference support is implied for them.

References and literal PATs are encrypted in SQLite by the existing Fernet credential store. Source responses still expose only `has_token`, which means a value is registered; it does not prove that a reference resolves or that a provider accepts it. Listing, replacing and deleting a source remain possible when its reference is unavailable.

## Explicit operator permission

Both reference mechanisms are disabled by default. The operator must configure the backend process independently of the HTTP request:

- `RANGE42_GIT_SECRET_ENV_ALLOWLIST=EXERCISE_GIT_PAT,OTHER_GIT_PAT` permits only those exact environment names. `env://EXERCISE_GIT_PAT` resolves that variable without expanding other variables or recursively resolving its contents.
- `RANGE42_GIT_SECRET_DIR=/run/range42/git-secrets` permits files inside that directory. For example, `file:///run/range42/git-secrets/catalog-pat` selects one file. The configured directory, descendants and secret file must be private to root/the API UID (no group/other permission bits); files must be regular, single-link files. Symlinks, including parent components, and traversal are refused. A reference to `/etc/backend.env` outside the configured directory cannot become outbound Git authentication.

Only place Git credentials intended for the registered, operator-approved repository hosts in the allowlisted namespace/directory. The separate `RANGE42_GIT_ALLOWED_HOSTS` policy still controls outbound Git endpoints. A shared API bearer is an administrative credential; this feature does not introduce per-user secret permissions or an external vault service.

Reference strings retain the existing API limit of 128 characters. Resolved values must be nonempty UTF-8 tokens, at most 8192 bytes, without leading/trailing whitespace or control characters. A file may end with one LF or CRLF; that terminator is removed. Files are opened relative to directory descriptors with no-follow flags; type, owner, mode, link count, size and modification identity are checked around two bounded reads from the same descriptor, whose bytes must agree. File size is limited to 8192 bytes including its optional newline. Unsupported mounts/layouts fail closed. Projected secret mounts using symlinks need an operator-managed private regular-file copy.

## Rotation and operation boundaries

Each operation resolves a source credential once. Catalog cache identity includes digests of both the registered reference and the resolved bytes. The same immutable source view supplies the Git clone, so rotation between cache lookup and worker execution cannot mix credentials. A new request reads the reference before any cache hit: changed bytes select another snapshot, and missing/denied/malformed references cannot fall back to a previous private snapshot. No resolved secret is persisted in the catalog cache or source row.

Rotate file credentials by replacing the regular file atomically with another private file. A replacement or rewrite detected during a read yields `GIT_CREDENTIAL_REFERENCE_CHANGED`, without returning partial data. Ordinary external edits to a service environment file do not modify a running process's environment: environment-reference rotation normally requires restarting the API with the new environment. No environment watcher is added.

An already-started Git operation continues with the captured credential. A project attempt retains that exact token privately for runner redaction, including its URL-encoded form, even if the reference changes before the runner starts. The existing attempt cleanup removes its redaction artifact. The in-memory scenario/source representations hide credentials from `repr`; they are never API DTOs. The database Fernet key itself is not rotated by this mechanism.

An unchanged credential does not make upstream ACL changes instantly observable through cached metadata. The existing 30-second cache TTL or explicit Source Refresh still applies to changes at the provider. Pagination remains separate requests, not a transaction across multiple credential/repository revisions.

## Errors and validation

Reference failures return HTTP 503 with one fixed code: `GIT_CREDENTIAL_REFERENCE_DENIED`, `GIT_CREDENTIAL_REFERENCE_UNAVAILABLE`, `GIT_CREDENTIAL_REFERENCE_INVALID`, or `GIT_CREDENTIAL_REFERENCE_CHANGED`. Messages do not contain variable names, file paths, resolved values, or underlying exceptions. Repository failures preserve the existing Git failure codes but omit untrusted remote stderr, which can echo a token outside an authenticated URL.

Focused tests use real local Git repositories, synthetic credentials and disposable private files. They cover literal compatibility, both rotation paths, key/clone identity, denied and missing references before cached reads, metadata recovery, linked/symlink/FIFO/public-file refusal, concurrent file changes, malformed content, project/bundle authentication, upstream error redaction and exact original runner redaction after rotation. No provider or shared guest mutation is part of these tests.

Python documents descriptor-relative file operations in [os.open](https://docs.python.org/3/library/os.html#os.open) and the process environment behavior in [os.environ](https://docs.python.org/3/library/os.html#os.environ).
