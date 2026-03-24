# Range42 Backend API

FastAPI application that orchestrates Proxmox infrastructure deployments by executing Ansible playbooks via `ansible-runner`. Part of the [Range42](https://github.com/range42) cyber range platform. Designed to sit behind a Kong API gateway that handles authentication and ACLs.

---

## Table of Contents

- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [API Documentation](#api-documentation)
- [Project Structure](#project-structure)
- [Architecture](#architecture)
- [Development](#development)
- [License](#license)

---

## Quick Start

### Option 1 -- Docker

```bash
docker compose up
```

Builds the image, installs dependencies and Ansible collections, and starts the API on port `8000`.

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

---

## API Documentation

Once the server is running, interactive docs are available at:

| Format       | URL                  |
| ------------ | -------------------- |
| Swagger UI   | `/docs/swagger`      |
| ReDoc        | `/docs/redoc`        |
| OpenAPI JSON | `/docs/openapi.json` |

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
python3 -m pytest tests/ -v
```

### Manual Testing

Curl scripts for every endpoint are available in `curl_utils/`:

```bash
# Example: list VMs
bash curl_utils/proxmox.vms.list.sh
```

### Code Conventions

- **Imports**: Absolute from `app.` (no relative imports except in `__init__`)
- **Naming**: `req` for request objects, `rc` for return codes, `extravars` for Ansible extra variables
- **HTTP codes**: 200 for Ansible success (rc=0), 500 for failure, 400 for validation errors
- **Commit style**: Conventional commits -- `feat(scope):`, `fix(scope):`, `docs:`, `refactor:`
- **Branch naming**: `feature/description`, `fix/description`, `release/x.y.z`

---

## License

[GPL-3.0](LICENSE)
