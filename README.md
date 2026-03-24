# Range42 Backend API

FastAPI application that orchestrates Proxmox infrastructure deployments by executing Ansible playbooks via `ansible-runner`. Part of the [Range42](https://github.com/range42) cyber range platform. Designed to sit behind a Kong API gateway that handles authentication and ACLs.

---

## Table of Contents

- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [API Documentation](#api-documentation)
  - [WebSocket API](#websocket-api)
- [Project Structure](#project-structure)
- [Architecture](#architecture)
- [Development](#development)
  - [Test Structure](#test-structure)
- [License](#license)

---

## Quick Start

### Option 1 -- Docker

```bash
docker compose up
```

Builds the image, installs dependencies and Ansible collections, and starts the API on port `8000`.

**Environment variables:** Configured via the host environment or a `.env` file. Required: at least one of `VAULT_PASSWORD_FILE` or `VAULT_PASSWORD` for vault-encrypted operations.

**Volumes:**
- `./app` -- Application source (read-only)
- `./playbooks` -- Ansible playbooks (read-only)
- `./inventory` -- Ansible inventory files (read-only)

**Health check:** The container pings `/docs/openapi.json` every 30s (5s timeout, 10s start period, 3 retries).

### Option 2 -- start.sh

```bash
./start.sh
```

The script resolves `PROJECT_ROOT_DIR`, sources `.env` if present, installs Ansible collections on first run, and launches uvicorn with `--reload`.

### Option 3 -- Manual (development)

```bash
# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install Python and Ansible dependencies
pip install -r requirements.txt
ansible-galaxy collection install -r requirements.yml -p ~/.ansible/collections

# Set required environment variables (or create a .env file)
export PROJECT_ROOT_DIR="$(pwd)"
export VAULT_PASSWORD_FILE="/path/to/vault-pass.txt"

# Start the dev server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## Configuration

All settings are read from environment variables in `app/core/config.py`. Nothing is hard-coded.

| Variable                           | Required | Description                               | Default                                  |
| ---------------------------------- | -------- | ----------------------------------------- | ---------------------------------------- |
| `PROJECT_ROOT_DIR`                 | Yes      | Absolute path to the project root         | `.` (cwd)                                |
| `VAULT_PASSWORD_FILE`              | Yes\*    | Path to the Ansible Vault password file   | --                                       |
| `VAULT_PASSWORD`                   | Yes\*    | Ansible Vault password as a string        | --                                       |
| `API_BACKEND_WWWAPP_PLAYBOOKS_DIR` | No       | Local playbooks directory                 | `PROJECT_ROOT_DIR/`                      |
| `API_BACKEND_PUBLIC_PLAYBOOKS_DIR` | No       | External playbooks repository path        | --                                       |
| `API_BACKEND_INVENTORY_DIR`        | No       | Ansible inventory directory               | `PROJECT_ROOT_DIR/inventory/`            |
| `API_BACKEND_VAULT_FILE`           | No       | Path to vault-encrypted variables file    | --                                       |
| `CORS_ORIGIN_REGEX`                | No       | Regex for allowed CORS origins            | `localhost` / `127.0.0.1` / `[::1]` only |
| `HOST`                             | No       | Server bind address                       | `0.0.0.0`                                |
| `PORT`                             | No       | Server listen port                        | `8000`                                   |
| `DEBUG`                            | No       | Enable debug mode (`true`, `1`, or `yes`) | `false`                                  |

> \*One of `VAULT_PASSWORD_FILE` or `VAULT_PASSWORD` must be set for vault-encrypted operations.

### Logging

The API uses Python's `logging` module with structured output. Log level is controlled by uvicorn:

```bash
uvicorn app.main:app --log-level debug   # verbose
uvicorn app.main:app --log-level info    # default
uvicorn app.main:app --log-level warning # quiet
```

When `DEBUG=true`, the app registers a custom 422 handler that logs full validation error details at `ERROR` level -- useful for debugging malformed requests during development.

---

## API Documentation

Once the server is running, interactive docs are available at:

| Format       | URL                  |
| ------------ | -------------------- |
| Swagger UI   | `/docs/swagger`      |
| ReDoc        | `/docs/redoc`        |
| OpenAPI JSON | `/docs/openapi.json` |

### WebSocket API

#### VM Status Stream

Real-time VM status updates via WebSocket. Polls the Proxmox API directly (not via Ansible) for low latency.

**URL:** `ws://host:8000/ws/vm-status`

**Query Parameters:**

| Parameter | Required | Description                          | Default             |
| --------- | -------- | ------------------------------------ | ------------------- |
| `node`    | No       | Proxmox node name to monitor         | Read from inventory |

**Authentication:** Proxmox API credentials are read from the backend's `inventory/hosts.yml` file. The frontend does not need to handle tokens.

#### Message Format

**Initial connection -- full state:**
```json
{
  "type": "full",
  "vms": [
    {
      "vmid": 100,
      "name": "my-vm",
      "status": "running",
      "cpu": 12.5,
      "mem": 2147483648,
      "maxmem": 4294967296,
      "uptime": 86400,
      "template": 0,
      "tags": "web;production"
    }
  ]
}
```

**Subsequent updates -- diff only:**
```json
{
  "type": "diff",
  "changes": {
    "100": { "type": "changed", "vmid": 100, "status": "stopped", "cpu": 0.0 },
    "102": { "type": "added", "vmid": 102, "name": "new-vm", "status": "running" },
    "101": { "type": "removed", "vmid": 101 }
  }
}
```

**Error:**
```json
{ "error": "Proxmox credentials not found in backend inventory" }
```

**Behavior:**
- Polls every 5 seconds
- Template VMs are excluded
- Status changes and CPU changes > 2% trigger a diff
- Connection closes on credential errors

---

## Project Structure

```
range42-backend-api/
|-- app/
|   |-- main.py                  # FastAPI app factory, CORS, vault lifespan
|   |-- core/
|   |   |-- config.py            # Centralized settings from env vars
|   |   |-- runner.py            # Ansible playbook execution engine
|   |   |-- extractor.py         # Structured result extraction from events
|   |   |-- vault.py             # Vault password file management
|   |   |-- exceptions.py        # Custom exception handlers
|   |-- routes/
|   |   |-- __init__.py          # Router assembly and prefix mapping
|   |   |-- vms.py               # VM lifecycle (list, start, stop, create, delete, clone)
|   |   |-- vm_config.py         # VM configuration (get config, set tags)
|   |   |-- snapshots.py         # VM snapshots (list, create, delete, revert)
|   |   |-- firewall.py          # Firewall (aliases, rules, enable/disable)
|   |   |-- network.py           # Network interfaces (VM and node level)
|   |   |-- storage.py           # Storage (list, download ISO, templates)
|   |   |-- bundles.py           # Predefined bundles (Ubuntu setup, Proxmox VMs)
|   |   |-- runner.py            # Generic bundle/scenario runner
|   |   |-- debug.py             # Debug endpoints (ping, test functions)
|   |   |-- ws_status.py         # WebSocket real-time VM status
|   |-- schemas/
|   |   |-- base.py              # Shared Pydantic base models
|   |   |-- vms.py               # VM request/response schemas
|   |   |-- vm_config.py         # VM config schemas
|   |   |-- snapshots.py         # Snapshot schemas
|   |   |-- firewall.py          # Firewall schemas
|   |   |-- network.py           # Network schemas
|   |   |-- storage.py           # Storage schemas
|   |   |-- bundles/             # Bundle-specific schemas
|   |   |-- debug/               # Debug endpoint schemas
|   |-- utils/
|   |   |-- checks_playbooks.py  # Playbook path validation and resolution
|   |   |-- checks_inventory.py  # Inventory path validation and resolution
|   |   |-- text_cleaner.py      # ANSI escape code stripper
|   |   |-- vm_id_name_resolver.py  # VM ID to name resolution via Ansible
|-- tests/                       # Pytest test suite
|-- curl_utils/                  # Manual testing curl scripts
|-- playbooks/                   # Local Ansible playbooks (generic, ping)
|-- inventory/                   # Ansible inventory files
|-- Dockerfile                   # Multi-stage Docker build
|-- docker-compose.yml           # Compose service definition
|-- start.sh                     # Development startup script
|-- requirements.txt             # Python dependencies
|-- requirements.yml             # Ansible Galaxy requirements
```

---

## Architecture

### Request Flow

```
HTTP Request
  --> FastAPI route handler
    --> Pydantic schema validation
      --> Path / inventory checks (checks_playbooks.py, checks_inventory.py)
        --> runner.py (ansible-runner in temp directory)
          --> Extract structured results (extractor.py)
            --> JSONResponse (rc + result or log lines)
```

### Key Design Decisions

- **Temp directory per run** -- Each playbook execution creates an isolated temp directory containing a copy of the playbook tree, inventory, and environment variables. The directory is cleaned up in a `finally` block to prevent leaks.

- **Vault lifecycle** -- On app startup, the lifespan context manager either reads `VAULT_PASSWORD_FILE` directly or writes `VAULT_PASSWORD` to a secure temp file. Both are cleaned up on shutdown.

- **Two response modes** -- Endpoints that accept `as_json` can return either structured data extracted from Ansible events (`"result"` key) or raw log lines (`"log_multiline"` array).

- **Path traversal protection** -- All playbook and inventory names are validated against `^[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*$` and resolved paths are checked with `is_relative_to()` to prevent directory traversal attacks.

- **No auth in this layer** -- Authentication, ACLs, and rate limiting are handled by the Kong API gateway in front of this API. CORS is restricted to localhost origins only.

### Route Prefixes

| Prefix                                  | Module                    | Purpose               |
| --------------------------------------- | ------------------------- | --------------------- |
| `/v0/admin/proxmox/vms/`                | `vms.py`                  | VM list and lifecycle |
| `/v0/admin/proxmox/vms/vm_id/`          | `vms.py`                  | Single VM operations  |
| `/v0/admin/proxmox/vms/vm_ids/`         | `vms.py`                  | Mass VM operations    |
| `/v0/admin/proxmox/vms/vm_id/config/`   | `vm_config.py`            | VM configuration      |
| `/v0/admin/proxmox/vms/vm_id/snapshot/` | `snapshots.py`            | VM snapshots          |
| `/v0/admin/proxmox/firewall/`           | `firewall.py`             | Firewall management   |
| `/v0/admin/proxmox/network/`            | `network.py`              | Network interfaces    |
| `/v0/admin/proxmox/storage/`            | `storage.py`              | Storage and ISOs      |
| `/v0/admin/run/bundles/`                | `bundles.py`, `runner.py` | Bundle execution      |
| `/v0/admin/run/scenarios/`              | `runner.py`               | Scenario execution    |
| `/v0/admin/debug/`                      | `debug.py`                | Debug/test endpoints  |
| `/ws/vm-status`                         | `ws_status.py`            | WebSocket VM status   |

---

## Development

### Running Tests

```bash
# All tests
python3 -m pytest tests/ -v

# Specific test file
python3 -m pytest tests/test_checks_playbooks.py -v

# Specific test
python3 -m pytest tests/test_ws_helpers.py::TestComputeDiff::test_detects_status_change -v
```

### Test Structure

| File                        | Covers                                                        |
| --------------------------- | ------------------------------------------------------------- |
| `test_api_smoke.py`         | App startup, OpenAPI schema, docs endpoints                   |
| `test_routes_registered.py` | Golden route reference (verifies all 69 routes are registered)|
| `test_config.py`            | `app/core/config.py` settings and defaults                    |
| `test_vault.py`             | `app/core/vault.py` VaultManager lifecycle                    |
| `test_runner.py`            | `app/core/runner.py` log building                             |
| `test_runner_internals.py`  | Runner helpers: envvars, cmdline, temp dir setup              |
| `test_extractor.py`         | `app/core/extractor.py` event parsing                         |
| `test_exceptions.py`        | Custom validation error formatting                            |
| `test_schemas.py`           | Pydantic request schema validation + backward-compat aliases  |
| `test_schemas_replies.py`   | Pydantic response schema validation                           |
| `test_checks_inventory.py`  | Inventory name validation and path traversal detection         |
| `test_checks_playbooks.py`  | Playbook name validation and path traversal detection          |
| `test_ws_helpers.py`        | WebSocket helpers: diff computation, credential loading        |
| `test_route_debug.py`       | Debug endpoint integration tests (mocked runner)               |
| `test_route_vms.py`         | VM endpoint integration tests (mocked runner)                  |

Route handler tests mock `run_playbook_core()` so no Ansible or Proxmox connection is needed.

The **golden route reference** (`tests/fixtures/routes_golden.json`) is a safety net that ensures refactoring never accidentally drops an endpoint. If you add or remove a route, update this file.

### Manual Testing

Curl scripts for every endpoint are available in `curl_utils/`.

### Code Conventions

- **Imports**: Absolute from `app.` (no relative imports except in `__init__`)
- **Naming**: `req` for request objects, `rc` for return codes, `extravars` for Ansible extra variables
- **HTTP codes**: 200 for Ansible success (rc=0), 500 for failure, 400 for validation errors
- **Commit style**: Conventional commits -- `feat(scope):`, `fix(scope):`, `docs:`, `refactor:`
- **Branch naming**: `feature/description`, `fix/description`, `release/x.y.z`

---

## License

[GPL-3.0](LICENSE)
