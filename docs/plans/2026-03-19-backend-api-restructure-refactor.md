# Range42 Backend API — Restructure, Refactor, Dockerize & Document

**Goal:** Restructure the FastAPI backend for clarity (#53), refactor for maintainability and best practices (#54), add Docker support (#51), and improve documentation (#52) — all while preserving 100% API compatibility with the deployer-ui frontend.

**Architecture:** The backend is a FastAPI app that orchestrates Proxmox infrastructure via Ansible playbooks. Every route follows the same pattern: validate request → build extravars → call `run_playbook_core()` → extract results → return JSON. The refactoring consolidates 86 near-identical route files into domain-grouped modules using shared base classes, replaces global state with dependency injection, and wraps everything in Docker for portable deployment.

**Tech Stack:** Python 3.12+, FastAPI 0.115, Pydantic v2, ansible-runner 2.4, uvicorn, httpx, Docker, docker-compose

**API Compatibility Contract:** The deployer-ui frontend expects these exact behaviors — DO NOT break them:
- All endpoints under `/v0/admin/proxmox/`, `/v0/admin/run/`, `/v0/admin/debug/`
- POST for all operations (including reads), DELETE for deletions
- Every request body accepts `proxmox_node`, `as_json` fields (plus `hosts`/`inventory` injected by UI)
- Response format: `{"rc": <int>, "result": [[...items]]}` when `as_json=True`
- Response format: `{"rc": <int>, "log_multiline": [...lines]}` when `as_json=False`
- HTTP 200 when `rc=0`, HTTP 500 when `rc!=0`
- 422 validation errors with `{"detail": [{"field":..., "msg":..., "type":...}]}` format
- WebSocket at `/ws/vm-status` with `full`/`diff` message types

**Related Issues:** #51 (Docker), #52 (Documentation), #53 (Restructure), #54 (Refactor)

---

## Phase 0: Preparation & Safety Net

### Task 0.1: Create Feature Branch and Baseline Tests

Before touching any code, establish a safety net.

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/test_api_smoke.py`
- Create: `pytest.ini`

- [ ] **Step 1: Add pytest to requirements**

Add to `requirements.txt`:
```
pytest==8.3.4
pytest-asyncio==0.24.0
httpx==0.27.2
```

(`httpx` is already there — just ensure pytest + pytest-asyncio are added)

- [ ] **Step 2: Create pytest.ini**

```ini
[pytest]
testpaths = tests
asyncio_mode = auto
```

- [ ] **Step 3: Create test conftest with FastAPI test client**

```python
# tests/conftest.py
import json
import os
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

_PROJECT_ROOT = str(Path(__file__).resolve().parents[1])

# Set ALL env vars that are read at module-import time (before importing app).
# Routes call Path(os.getenv("PROJECT_ROOT_DIR")).resolve() at import time — missing vars crash immediately.
os.environ.setdefault("PROJECT_ROOT_DIR", _PROJECT_ROOT)
os.environ.setdefault("API_BACKEND_WWWAPP_PLAYBOOKS_DIR", _PROJECT_ROOT)
os.environ.setdefault("API_BACKEND_PUBLIC_PLAYBOOKS_DIR", _PROJECT_ROOT)  # needed by checks_playbooks.py
os.environ.setdefault("API_BACKEND_INVENTORY_DIR", str(Path(_PROJECT_ROOT) / "inventory"))
# Vault vars are only needed at runtime (lifespan), not at import time — safe to skip in tests.

@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


@pytest.fixture(scope="session")
def openapi_schema():
    """Load OpenAPI schema once for all tests. Used as golden reference for route verification."""
    from app.main import app
    client = TestClient(app)
    resp = client.get("/docs/openapi.json")
    return resp.json()
```

- [ ] **Step 4: Save current OpenAPI schema as golden reference**

Before any restructuring, export the current schema to use as regression baseline:

```bash
cd /home/ppa/projects/range42-base/range42-backend-api
mkdir -p tests/fixtures
# Start the app briefly to export schema (or use TestClient)
python -c "
import json, os
from pathlib import Path
os.environ.setdefault('PROJECT_ROOT_DIR', str(Path('.').resolve()))
os.environ.setdefault('API_BACKEND_WWWAPP_PLAYBOOKS_DIR', str(Path('.').resolve()))
os.environ.setdefault('API_BACKEND_PUBLIC_PLAYBOOKS_DIR', str(Path('.').resolve()))
os.environ.setdefault('API_BACKEND_INVENTORY_DIR', str(Path('.').resolve() / 'inventory'))
from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)
schema = client.get('/docs/openapi.json').json()
# Save just the paths (method -> path pairs) as the golden reference
routes = {}
for path, methods in schema.get('paths', {}).items():
    for method in methods:
        if method.upper() in ('GET', 'POST', 'PUT', 'DELETE', 'PATCH'):
            routes.setdefault(path, []).append(method.upper())
with open('tests/fixtures/routes_golden.json', 'w') as f:
    json.dump(routes, f, indent=2, sort_keys=True)
print(f'Saved {sum(len(v) for v in routes.values())} routes across {len(routes)} paths')
"
```

- [ ] **Step 5: Write smoke tests that verify all current routes exist**

```python
# tests/test_api_smoke.py
"""Smoke tests: verify all API routes are registered and respond to requests.
These tests do NOT call Ansible — they only verify the FastAPI route table.
Route verification uses the auto-generated golden reference (tests/fixtures/routes_golden.json),
NOT a hand-written list, to catch ALL endpoints including dynamic bundle/scenario routes."""

import json
from pathlib import Path


def test_app_starts(client):
    """App boots without error."""
    assert client is not None


def test_openapi_schema_loads(client):
    """OpenAPI schema is generated without error."""
    resp = client.get("/docs/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert schema["info"]["title"] == "CR42 - API"
    assert schema["info"]["version"] == "v0.1"


def test_swagger_docs_available(client):
    resp = client.get("/docs/swagger")
    assert resp.status_code == 200


def test_redoc_docs_available(client):
    resp = client.get("/docs/redoc")
    assert resp.status_code == 200


def test_all_routes_match_golden_reference(client):
    """Every endpoint from the pre-restructure golden reference must still exist.
    This catches routes that are silently dropped during file consolidation.
    The golden reference is auto-generated (Task 0.1 Step 4), NOT hand-coded."""
    golden_path = Path(__file__).parent / "fixtures" / "routes_golden.json"
    assert golden_path.exists(), f"Golden reference not found at {golden_path}. Run Step 4 first."

    with open(golden_path) as f:
        golden_routes = json.load(f)

    resp = client.get("/docs/openapi.json")
    schema = resp.json()
    registered = {}
    for path, methods in schema.get("paths", {}).items():
        for method in methods:
            if method.upper() in ("GET", "POST", "PUT", "DELETE", "PATCH"):
                registered.setdefault(path, []).append(method.upper())

    missing = []
    for path, methods in golden_routes.items():
        for method in methods:
            if method not in registered.get(path, []):
                missing.append(f"{method} {path}")

    assert not missing, f"Missing {len(missing)} routes after restructure:\n" + "\n".join(sorted(missing))


def test_no_routes_accidentally_added(client):
    """Verify no new routes were accidentally introduced during restructuring."""
    golden_path = Path(__file__).parent / "fixtures" / "routes_golden.json"
    if not golden_path.exists():
        return  # Skip if golden reference not yet generated

    with open(golden_path) as f:
        golden_routes = json.load(f)

    resp = client.get("/docs/openapi.json")
    schema = resp.json()

    golden_set = set()
    for path, methods in golden_routes.items():
        for method in methods:
            golden_set.add((method, path))

    current_set = set()
    for path, methods in schema.get("paths", {}).items():
        for method in methods:
            if method.upper() in ("GET", "POST", "PUT", "DELETE", "PATCH"):
                current_set.add((method.upper(), path))

    added = current_set - golden_set
    if added:
        # Not a hard failure — just informational
        print(f"INFO: {len(added)} new routes added: {sorted(added)}")
```

- [ ] **Step 5: Run smoke tests to establish baseline**

Run: `cd /home/ppa/projects/range42-base/range42-backend-api && python -m pytest tests/test_api_smoke.py -v`
Expected: All tests PASS (or document which fail due to missing env vars — those are acceptable at this stage)

- [ ] **Step 6: Commit baseline tests**

```bash
git add tests/ pytest.ini requirements.txt
git commit -m "test: add smoke tests as safety net before restructuring (#53, #54)"
```

---

## Phase 1: Project Restructure (#53)

Goal: Flatten deep nesting, group by domain, separate core logic from scripts/config.

### Current Structure (problematic)
```
app/
  routes/v0/proxmox/vms/vm_id/config/vm_get_config.py   # 7 levels deep
  routes/v0/proxmox/firewall/enable_firewall_vm.py
  schemas/proxmox/vm_id/start_stop_resume_pause.py       # mirrors route nesting
```

### Target Structure
```
app/
  core/                     # Core logic (was scattered)
    config.py               # App configuration (env vars, settings)
    runner.py               # Ansible playbook runner (moved from app/)
    extractor.py            # Event extraction (moved from app/extract_actions.py)
    vault.py                # Vault management (moved from app/vault/)
    exceptions.py           # Custom exception handlers

  routes/                   # Flattened, domain-grouped
    __init__.py             # Router assembly
    vms.py                  # All VM lifecycle routes (was 20+ files)
    vm_config.py            # VM config routes (was 5 files)
    snapshots.py            # Snapshot routes (was 4 files)
    firewall.py             # Firewall routes (was 13 files)
    network.py              # Network routes (was 6 files)
    storage.py              # Storage routes (was 4 files)
    bundles.py              # Core bundle routes — Ubuntu + Proxmox (was split across files)
    runner.py               # Dynamic bundle/scenario runner (/{name}/run endpoints)
    debug.py                # Debug routes (was 2 files)
    ws_status.py            # WebSocket (stays as-is)

  schemas/                  # Flattened, domain-grouped
    base.py                 # Shared base models
    vms.py                  # VM schemas (was 10+ files)
    vm_config.py            # VM config schemas
    snapshots.py            # Snapshot schemas
    firewall.py             # Firewall schemas
    network.py              # Network schemas
    storage.py              # Storage schemas
    bundles.py              # Bundle/scenario schemas
    debug.py                # Debug schemas

  utils/                    # Stays mostly as-is
    __init__.py
    checks_playbooks.py
    checks_inventory.py
    text_cleaner.py
    vm_id_name_resolver.py

curl_utils/                 # Stays as-is (testing scripts)
playbooks/                  # Stays as-is
inventory/                  # Stays as-is
```

### Task 1.1: Create `app/core/config.py` — Centralized Configuration

**Files:**
- Create: `app/core/__init__.py`
- Create: `app/core/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing test for config module**

```python
# tests/test_config.py
import os
from pathlib import Path


def test_settings_loads_from_env(monkeypatch):
    monkeypatch.setenv("PROJECT_ROOT_DIR", "/tmp/test-project")
    monkeypatch.setenv("CORS_ORIGIN_REGEX", r"^https?://example\.com$")

    # Force reimport
    import importlib
    import app.core.config as config_mod
    importlib.reload(config_mod)
    from app.core.config import settings

    assert settings.project_root == Path("/tmp/test-project")
    assert settings.cors_origin_regex == r"^https?://example\.com$"


def test_settings_defaults(monkeypatch):
    monkeypatch.setenv("PROJECT_ROOT_DIR", "/tmp/test-project")
    monkeypatch.delenv("CORS_ORIGIN_REGEX", raising=False)

    import importlib
    import app.core.config as config_mod
    importlib.reload(config_mod)
    from app.core.config import settings

    assert "localhost" in settings.cors_origin_regex
    assert settings.debug is False


def test_settings_playbook_path(monkeypatch):
    monkeypatch.setenv("PROJECT_ROOT_DIR", "/tmp/test-project")

    import importlib
    import app.core.config as config_mod
    importlib.reload(config_mod)
    from app.core.config import settings

    assert settings.playbook_path == Path("/tmp/test-project/playbooks/generic.yml")
    assert settings.inventory_name == "hosts"
```

- [ ] **Step 2: Run test, verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.core'`

- [ ] **Step 3: Implement config module**

```python
# app/core/__init__.py
```

```python
# app/core/config.py
"""Centralized application configuration. All env vars read here, nowhere else."""

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """Immutable application settings loaded from environment variables."""

    project_root: Path = field(default_factory=lambda: Path(os.getenv("PROJECT_ROOT_DIR", ".")).resolve())

    # Playbook paths
    wwwapp_playbooks_dir: str = field(default_factory=lambda: os.getenv("API_BACKEND_WWWAPP_PLAYBOOKS_DIR", ""))
    public_playbooks_dir: str = field(default_factory=lambda: os.getenv("API_BACKEND_PUBLIC_PLAYBOOKS_DIR", ""))
    inventory_dir: str = field(default_factory=lambda: os.getenv("API_BACKEND_INVENTORY_DIR", ""))
    vault_file: str = field(default_factory=lambda: os.getenv("API_BACKEND_VAULT_FILE", ""))

    # Vault credentials
    vault_password_file: str = field(default_factory=lambda: os.getenv("VAULT_PASSWORD_FILE", ""))
    vault_password: str = field(default_factory=lambda: os.getenv("VAULT_PASSWORD", ""))

    # CORS
    cors_origin_regex: str = field(
        default_factory=lambda: os.getenv(
            "CORS_ORIGIN_REGEX",
            r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$",
        )
    )

    # Server
    host: str = field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.getenv("PORT", "8000")))
    debug: bool = field(default_factory=lambda: os.getenv("DEBUG", "").lower() in ("1", "true", "yes"))

    @property
    def playbook_path(self) -> Path:
        return self.project_root / "playbooks" / "generic.yml"

    @property
    def inventory_name(self) -> str:
        return "hosts"


settings = Settings()
```

- [ ] **Step 4: Run test, verify it passes**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/core/ tests/test_config.py
git commit -m "refactor: add centralized config module (#53, #54)"
```

### Task 1.2: Create `app/core/vault.py` — Vault with Dependency Injection

**Files:**
- Create: `app/core/vault.py`
- Test: `tests/test_vault.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_vault.py
from pathlib import Path
from app.core.vault import VaultManager


def test_vault_manager_starts_empty():
    vm = VaultManager()
    assert vm.get_vault_path() is None


def test_vault_manager_set_and_get():
    vm = VaultManager()
    p = Path("/tmp/test-vault.txt")
    vm.set_vault_path(p)
    assert vm.get_vault_path() == p


def test_vault_manager_reset():
    vm = VaultManager()
    vm.set_vault_path(Path("/tmp/test-vault.txt"))
    vm.set_vault_path(None)
    assert vm.get_vault_path() is None
```

- [ ] **Step 2: Run test, verify it fails**

Run: `python -m pytest tests/test_vault.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement VaultManager class**

```python
# app/core/vault.py
"""Vault password management. No global state — uses a class instance."""

from pathlib import Path


class VaultManager:
    """Manages the Ansible vault password file path."""

    def __init__(self) -> None:
        self._vault_pass_path: Path | None = None

    def set_vault_path(self, p: Path | None) -> None:
        self._vault_pass_path = p

    def get_vault_path(self) -> Path | None:
        return self._vault_pass_path
```

- [ ] **Step 4: Run test, verify it passes**

Run: `python -m pytest tests/test_vault.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/core/vault.py tests/test_vault.py
git commit -m "refactor: add VaultManager class replacing global state (#54)"
```

### Task 1.3: Create `app/core/runner.py` — Clean Playbook Runner

**Files:**
- Create: `app/core/runner.py`
- Create: `app/core/extractor.py`
- Test: `tests/test_runner.py`
- Test: `tests/test_extractor.py`

- [ ] **Step 1: Write failing test for extractor**

```python
# tests/test_extractor.py
from app.core.extractor import extract_action_results


def test_extract_finds_matching_action():
    events = [
        {"event": "runner_on_ok", "event_data": {"res": {"vm_list": [{"vmid": 100}]}}},
        {"event": "runner_on_ok", "event_data": {"res": {"other_action": "data"}}},
    ]
    result = extract_action_results(events, "vm_list")
    assert result == [[{"vmid": 100}]]


def test_extract_returns_empty_for_no_match():
    events = [
        {"event": "runner_on_ok", "event_data": {"res": {"other": "data"}}},
    ]
    result = extract_action_results(events, "vm_list")
    assert result == []


def test_extract_skips_non_ok_events():
    events = [
        {"event": "runner_on_failed", "event_data": {"res": {"vm_list": [{"vmid": 100}]}}},
    ]
    result = extract_action_results(events, "vm_list")
    assert result == []


def test_extract_handles_empty_events():
    assert extract_action_results([], "vm_list") == []


def test_extract_handles_missing_event_data():
    events = [{"event": "runner_on_ok"}]
    result = extract_action_results(events, "vm_list")
    assert result == []
```

- [ ] **Step 2: Run test, verify it fails**

Run: `python -m pytest tests/test_extractor.py -v`
Expected: FAIL

- [ ] **Step 3: Implement extractor**

```python
# app/core/extractor.py
"""Extract structured results from Ansible runner events."""


def extract_action_results(events: list[dict], action_to_search: str) -> list:
    """Find all runner_on_ok events containing the specified action key."""
    out = []
    for ev in events:
        if ev.get("event") != "runner_on_ok":
            continue
        res = (ev.get("event_data") or {}).get("res")
        if isinstance(res, dict) and action_to_search in res:
            out.append(res[action_to_search])
    return out
```

- [ ] **Step 4: Run extractor tests, verify they pass**

Run: `python -m pytest tests/test_extractor.py -v`
Expected: PASS

- [ ] **Step 5: Write failing test for runner build_logs**

```python
# tests/test_runner.py
from app.core.runner import build_logs


def test_build_logs_extracts_stdout():
    events = [
        {"stdout": "PLAY [all] ***"},
        {"stdout": "TASK [ping] ***"},
        {"other": "data"},
        {"stdout": "ok: [host1]"},
    ]
    log_ansi, log_plain = build_logs(events)
    assert "PLAY [all]" in log_ansi
    assert "ok: [host1]" in log_plain


def test_build_logs_empty_events():
    log_ansi, log_plain = build_logs([])
    assert log_ansi == ""
    assert log_plain == ""


def test_build_logs_strips_ansi():
    events = [{"stdout": "\x1b[32mok\x1b[0m: [host1]"}]
    log_ansi, log_plain = build_logs(events)
    assert "\x1b[32m" in log_ansi
    assert "\x1b[32m" not in log_plain
    assert "ok" in log_plain
```

- [ ] **Step 6: Implement runner module**

```python
# app/core/runner.py
"""Ansible playbook runner. Handles temp dir lifecycle, env vars, and execution."""

import logging
import os
import shutil
import tempfile
from pathlib import Path

from ansible_runner import run as ansible_run

from app.core.vault import VaultManager
from app.utils.text_cleaner import strip_ansi

logger = logging.getLogger(__name__)

# Module-level vault manager instance. Set by app.main during lifespan startup.
# Routes import run_playbook_core() and never need to know about vault internals.
vault_manager = VaultManager()


def build_logs(events: list[dict]) -> tuple[str, str]:
    """Build ansible logs with and without ANSI escape codes."""
    lines = [ev["stdout"] for ev in events if ev.get("stdout")]
    text_ansi = "\n".join(lines).strip()
    return text_ansi, strip_ansi(text_ansi)


def _build_envvars(vault_manager: VaultManager) -> dict[str, str]:
    """Build Ansible environment variables dict."""
    home_collections = os.path.expanduser("~/.ansible/collections")
    sys_collections = "/usr/share/ansible/collections"
    coll_paths = f"{home_collections}:{sys_collections}"

    envvars = {
        "ANSIBLE_HOST_KEY_CHECKING": "True",
        "ANSIBLE_DEPRECATION_WARNINGS": "False",
        "ANSIBLE_INVENTORY_ENABLED": "yaml,ini",
        "PYTHONWARNINGS": "ignore::DeprecationWarning",
        "ANSIBLE_ROLES_PATH": os.environ.get("ANSIBLE_ROLES_PATH", ""),
        "ANSIBLE_FILTER_PLUGINS": os.environ.get("ANSIBLE_FILTER_PLUGINS", ""),
        "ANSIBLE_COLLECTIONS_PATH": os.environ.get("ANSIBLE_COLLECTIONS_PATH", coll_paths),
        "ANSIBLE_COLLECTIONS_PATHS": os.environ.get("ANSIBLE_COLLECTIONS_PATHS", coll_paths),
        "ANSIBLE_LIBRARY": os.environ.get("ANSIBLE_LIBRARY", ""),
    }

    # Vault env vars
    vault_pw_file = os.getenv("VAULT_PASSWORD_FILE")
    if vault_pw_file:
        envvars["ANSIBLE_VAULT_PASSWORD_FILE"] = vault_pw_file
    elif vault_manager.get_vault_path():
        envvars["ANSIBLE_VAULT_PASSWORD_FILE"] = str(vault_manager.get_vault_path())

    ansible_config = os.getenv("ANSIBLE_CONFIG")
    if ansible_config:
        envvars["ANSIBLE_CONFIG"] = ansible_config

    return envvars


def _setup_temp_dir(
    inventory: Path, playbook: Path, vault_manager: VaultManager
) -> tuple[Path, Path, Path]:
    """Create temp execution dir with playbook tree and inventory copy.
    Returns: (tmp_dir, inventory_dest, playbook_relative_path)
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="runner-"))
    project_dir = tmp_dir / "project"
    inventory_dir = tmp_dir / "inventory"
    project_dir.mkdir(parents=True, exist_ok=True)
    inventory_dir.mkdir(parents=True, exist_ok=True)

    # Copy playbook tree
    src_dir = playbook.parent
    dst_dir = project_dir / src_dir.name
    shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)

    play_rel = (dst_dir / playbook.name).relative_to(project_dir)
    inv_dest = inventory_dir / inventory.name
    shutil.copy(inventory, inv_dest)

    # Write envvars file
    envvars = _build_envvars(vault_manager)
    env_dir = tmp_dir / "env"
    env_dir.mkdir(parents=True, exist_ok=True)
    (env_dir / "envvars").write_text(
        "\n".join(f"{k}={v}" for k, v in envvars.items()) + "\n"
    )

    return tmp_dir, inv_dest, play_rel


def _build_cmdline(vault_manager: VaultManager, cmdline: str | None, tags: str | None) -> str | None:
    """Build ansible-playbook command line arguments."""
    if not cmdline:
        vf = os.getenv("VAULT_PASSWORD_FILE")
        if not vf:
            vp = vault_manager.get_vault_path()
            vf = str(vp) if vp else None
        if vf:
            cmdline = f'--vault-password-file "{vf}"'

    vars_file = os.getenv("API_BACKEND_VAULT_FILE")
    if vars_file:
        cmdline = f'{(cmdline or "").strip()} -e "@{vars_file}"'.strip()

    if tags:
        cmdline = f'{(cmdline or "").strip()} --tags {tags}'.strip()

    return cmdline or None


def run_playbook_core(
    playbook: Path,
    inventory: Path,
    limit: str | None = None,
    tags: str | None = None,
    cmdline: str | None = None,
    extravars: dict | None = None,
    quiet: bool = False,
) -> tuple[int, list[dict], str, str]:
    """Execute an Ansible playbook and return (rc, events, log_plain, log_ansi).

    Uses the module-level `vault_manager` instance, which is set during app lifespan.
    Routes call this function directly — no need to pass vault_manager explicitly.
    """
    tmp_dir, inv_dest, play_rel = _setup_temp_dir(inventory, playbook, vault_manager)
    try:
        final_cmdline = _build_cmdline(vault_manager, cmdline, tags)

        r = ansible_run(
            private_data_dir=str(tmp_dir),
            playbook=str(play_rel),
            inventory=str(inv_dest),
            streamer="json",
            limit=limit,
            cmdline=final_cmdline,
            extravars=extravars or {},
            quiet=quiet,
        )

        events = list(r.events) if hasattr(r, "events") else []
        log_ansi, log_plain = build_logs(events)
        return r.rc, events, log_plain, log_ansi
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
```

- [ ] **Step 7: Run runner tests, verify they pass**

Run: `python -m pytest tests/test_runner.py tests/test_extractor.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add app/core/runner.py app/core/extractor.py tests/test_runner.py tests/test_extractor.py
git commit -m "refactor: add clean runner and extractor in app/core/ (#53, #54)"
```

### Task 1.4: Create `app/core/exceptions.py` — Exception Handlers

**Files:**
- Create: `app/core/exceptions.py`
- Test: `tests/test_exceptions.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_exceptions.py
import json
from unittest.mock import AsyncMock, MagicMock
from app.core.exceptions import make_validation_error_detail


def test_make_validation_error_detail():
    error = {
        "loc": ("body", "vm_id"),
        "msg": "field required",
        "type": "missing",
        "input": None,
        "ctx": None,
    }
    detail = make_validation_error_detail(error)
    assert detail["field"] == "body.vm_id"
    assert detail["msg"] == "field required"
    assert detail["type"] == "missing"
```

- [ ] **Step 2: Run test, verify it fails**

Run: `python -m pytest tests/test_exceptions.py -v`
Expected: FAIL

- [ ] **Step 3: Implement exceptions module**

```python
# app/core/exceptions.py
"""Custom exception handlers for FastAPI."""

import json
import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def make_validation_error_detail(err: dict) -> dict:
    """Convert a Pydantic validation error to the response format the UI expects."""
    return {
        "field": ".".join(str(p) for p in err.get("loc", [])),
        "msg": err.get("msg", ""),
        "type": err.get("type", ""),
        "input": err.get("input", None),
        "ctx": err.get("ctx", None),
    }


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Verbose 422 handler with debug logging. Matches deployer-ui expected format."""
    logger.error("422 on %s %s %s", request.method, request.url.path, request.url.query)

    try:
        raw = await request.body()
        if raw:
            body_text = raw.decode("utf-8", "ignore")
            if body_text.strip():
                try:
                    parsed = json.loads(body_text)
                    logger.error("Request body:\n%s", json.dumps(parsed, indent=2, ensure_ascii=False))
                except json.JSONDecodeError:
                    logger.error("Request body (raw): %s", body_text)
            else:
                logger.error("Request body: <empty>")
    except Exception:
        logger.exception("Failed to read request body.")

    details = []
    for err in exc.errors():
        detail = make_validation_error_detail(err)
        logger.error("field=%s | msg=%s | type=%s", detail["field"], detail["msg"], detail["type"])
        details.append(detail)

    return JSONResponse(status_code=422, content={"detail": details})
```

- [ ] **Step 4: Run test, verify it passes**

Run: `python -m pytest tests/test_exceptions.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/core/exceptions.py tests/test_exceptions.py
git commit -m "refactor: extract exception handlers to app/core/exceptions (#54)"
```

### Task 1.5: Consolidate Schemas — Flatten into Domain Files

This task consolidates 52 schema files into 8 domain-grouped files.

**Files:**
- Create: `app/schemas/vms.py`
- Create: `app/schemas/vm_config.py`
- Create: `app/schemas/snapshots.py`
- Create: `app/schemas/firewall.py`
- Create: `app/schemas/network.py`
- Create: `app/schemas/storage.py`
- Create: `app/schemas/bundles.py`
- Modify: `app/schemas/base.py`
- Modify: `app/schemas/debug/ping.py` (keep as-is, just update imports later)
- Test: `tests/test_schemas.py`

**IMPORTANT:** Keep old schema files in place with re-exports during migration. Remove them only after all routes are updated.

- [ ] **Step 1: Write failing test for consolidated schemas**

```python
# tests/test_schemas.py
"""Verify consolidated schemas match the original field definitions."""
from pydantic import ValidationError
import pytest


def test_vm_list_request():
    from app.schemas.vms import VmListRequest
    req = VmListRequest(proxmox_node="px-testing")
    assert req.proxmox_node == "px-testing"
    assert req.as_json is True


def test_vm_action_request_validates_vm_id():
    from app.schemas.vms import VmActionRequest
    req = VmActionRequest(proxmox_node="px-testing", vm_id="100")
    assert req.vm_id == "100"


def test_vm_action_request_rejects_invalid_vm_id():
    from app.schemas.vms import VmActionRequest
    with pytest.raises(ValidationError):
        VmActionRequest(proxmox_node="px-testing", vm_id="abc")


def test_vm_create_request():
    from app.schemas.vms import VmCreateRequest
    req = VmCreateRequest(
        proxmox_node="px-testing",
        vm_id="200",
        vm_name="test-vm",
    )
    assert req.vm_name == "test-vm"
    assert req.vm_cpu is None  # optional


def test_vm_clone_request():
    from app.schemas.vms import VmCloneRequest
    req = VmCloneRequest(
        proxmox_node="px-testing",
        vm_id="100",
        vm_new_id="200",
        vm_name="cloned-vm",
    )
    assert req.vm_new_id == "200"


def test_proxmox_node_rejects_special_chars():
    from app.schemas.vms import VmListRequest
    with pytest.raises(ValidationError):
        VmListRequest(proxmox_node="node; rm -rf /")


def test_firewall_rule_request():
    from app.schemas.firewall import FirewallRuleApplyRequest
    req = FirewallRuleApplyRequest(
        proxmox_node="px-testing",
        vm_id="100",
        vm_fw_action="ACCEPT",
        vm_fw_type="in",
    )
    assert req.vm_fw_action == "ACCEPT"


def test_network_vm_add_request():
    from app.schemas.network import VmNetworkAddRequest
    req = VmNetworkAddRequest(
        proxmox_node="px-testing",
        vm_id="100",
        iface_bridge="vmbr100",
    )
    assert req.iface_bridge == "vmbr100"


# --- Bundle schemas (complex — have nested per-VM models) ---

def test_bundle_ubuntu_docker_request():
    """Ubuntu install bundle schemas must be preserved."""
    from app.schemas.bundles import DockerInstallRequest
    req = DockerInstallRequest(proxmox_node="px-testing")
    assert req.proxmox_node == "px-testing"


def test_bundle_proxmox_create_vms_request():
    """Proxmox bundle schemas have nested VMs dict — must preserve structure."""
    from app.schemas.bundles import CreateVmsAdminRequest
    # These schemas have a 'vms' field with per-VM nested models
    # The exact field names must match what admin_run_bundles_core routes expect
    assert hasattr(CreateVmsAdminRequest, "model_fields")
```

**NOTE on bundle schema files to read:** Before consolidating, read ALL of these:
- `app/schemas/bundles/core/linux/ubuntu/install/docker.py`
- `app/schemas/bundles/core/linux/ubuntu/install/docker_compose.py`
- `app/schemas/bundles/core/linux/ubuntu/install/basic_packages.py`
- `app/schemas/bundles/core/linux/ubuntu/install/dot_files.py`
- `app/schemas/bundles/core/linux/ubuntu/configure/add_user.py`
- `app/schemas/bundles/core/proxmox/configure/default/vms/create_vms_admin_default.py`
- `app/schemas/bundles/core/proxmox/configure/default/vms/create_vms_student_default.py`
- `app/schemas/bundles/core/proxmox/configure/default/vms/create_vms_vuln_default.py`
- `app/schemas/bundles/core/proxmox/configure/default/vms/revert_snapshot_default.py`
- `app/schemas/bundles/core/proxmox/configure/default/vms/start_stop_resume_pause_default.py`

- [ ] **Step 2: Run tests, verify they fail**

Run: `python -m pytest tests/test_schemas.py -v`
Expected: FAIL

- [ ] **Step 3: Read every existing schema file to extract field definitions**

Read all files under `app/schemas/` to understand the exact Pydantic field definitions, patterns, and defaults. You must preserve EVERY field exactly as defined — the deployer-ui depends on these.

- [ ] **Step 4: Create consolidated `app/schemas/vms.py`**

Consolidate from:
- `app/schemas/proxmox/vm_list.py` (Request_ProxmoxVms_VmList, Reply_ProxmoxVmList)
- `app/schemas/proxmox/vm_list_usage.py`
- `app/schemas/proxmox/vm_id/start_stop_resume_pause.py`
- `app/schemas/proxmox/vm_id/create.py`
- `app/schemas/proxmox/vm_id/delete.py`
- `app/schemas/proxmox/vm_id/clone.py`
- `app/schemas/proxmox/vm_id/mass_delete.py`
- `app/schemas/proxmox/vm_id/mass_start_stop_resume_pause.py`

Keep both NEW names (e.g., `VmListRequest`) and OLD names (e.g., `Request_ProxmoxVms_VmList = VmListRequest`) as aliases so existing route imports don't break during migration.

- [ ] **Step 5: Create consolidated `app/schemas/vm_config.py`**

Consolidate from `app/schemas/proxmox/vm_id/config/` — all 5 files.

- [ ] **Step 6: Create consolidated `app/schemas/snapshots.py`**

Consolidate from `app/schemas/proxmox/vm_id/snapshot/` — all 4 files.

- [ ] **Step 7: Create consolidated `app/schemas/firewall.py`**

Consolidate from `app/schemas/proxmox/firewall/` — all files.

- [ ] **Step 8: Create consolidated `app/schemas/network.py`**

Consolidate from `app/schemas/proxmox/network/` — all files.

- [ ] **Step 9: Create consolidated `app/schemas/storage.py`**

Consolidate from `app/schemas/proxmox/storage/` — all files.

- [ ] **Step 10: Create consolidated `app/schemas/bundles.py`**

Consolidate from `app/schemas/bundles/` — all files.

- [ ] **Step 11: Update `app/schemas/base.py`**

Keep existing classes, add a shared `ProxmoxBaseRequest` that other schemas inherit from:

```python
# Add to app/schemas/base.py
from pydantic import BaseModel, Field


class ProxmoxBaseRequest(BaseModel):
    """Base request model with fields common to all Proxmox operations."""
    proxmox_node: str = Field(..., pattern=r"^[A-Za-z0-9-]*$")
    as_json: bool = Field(default=True)
```

- [ ] **Step 12: Run schema tests, verify they pass**

Run: `python -m pytest tests/test_schemas.py -v`
Expected: PASS

- [ ] **Step 13: Commit**

```bash
git add app/schemas/ tests/test_schemas.py
git commit -m "refactor: consolidate 52 schema files into domain-grouped modules (#53)"
```

### Task 1.6: Consolidate Routes — Flatten into Domain Files

This is the largest task. Each domain's 5-20 route files collapse into one file.

**CRITICAL:** All route paths, HTTP methods, tags, summaries, and response models must stay IDENTICAL. Only the file organization changes.

**Files:**
- Create: `app/routes/vms.py`
- Create: `app/routes/vm_config.py`
- Create: `app/routes/snapshots.py`
- Create: `app/routes/firewall.py`
- Create: `app/routes/network.py`
- Create: `app/routes/storage.py`
- Create: `app/routes/bundles.py`
- Create: `app/routes/runner.py` (dynamic {name}/run endpoints)
- Create: `app/routes/debug.py`
- Modify: `app/routes/__init__.py`
- Keep: `app/routes/ws_status.py` (already well-structured)
- Test: `tests/test_routes_registered.py`

- [ ] **Step 1: Write test that verifies route registration**

```python
# tests/test_routes_registered.py
"""After consolidation, verify all routes are still registered with correct methods and paths.
Uses the golden reference file generated in Task 0.1 Step 4 — NOT a hand-coded list."""

import json
from pathlib import Path


def test_all_routes_preserved(client):
    """After restructure, every route from the golden reference must still exist.
    This catches all endpoints including bundle/scenario dynamic routes."""
    golden_path = Path(__file__).parent / "fixtures" / "routes_golden.json"
    assert golden_path.exists(), "Golden reference missing — run Task 0.1 Step 4 first"

    with open(golden_path) as f:
        golden_routes = json.load(f)

    resp = client.get("/docs/openapi.json")
    schema = resp.json()
    registered = {}
    for path, methods in schema.get("paths", {}).items():
        for method in methods:
            if method.upper() in ("GET", "POST", "PUT", "DELETE", "PATCH"):
                registered.setdefault(path, []).append(method.upper())

    missing = []
    for path, methods in golden_routes.items():
        for method in methods:
            if method not in registered.get(path, []):
                missing.append(f"{method} {path}")

    assert not missing, f"Missing {len(missing)} routes after restructure:\n" + "\n".join(sorted(missing))
```

- [ ] **Step 2: Consolidate VM routes into `app/routes/vms.py`**

Read every file under `app/routes/v0/proxmox/vms/` and consolidate into one file. Each route handler function must:
1. Keep the same `@router.post(path=..., summary=..., tags=..., response_model=...)` decorator
2. Use `run_playbook_core()` from `app.core.runner` (with vault_manager parameter)
3. Use `extract_action_results()` from `app.core.extractor`
4. Use `settings` from `app.core.config` instead of `Path(os.getenv("PROJECT_ROOT_DIR"))`
5. Keep the same response format (rc + result or rc + log_multiline)

The consolidated file should define:
- A `_run_proxmox_action()` helper that encapsulates the common pattern (since all VM routes follow the same flow)
- Individual route handlers that call the helper with their specific action name and extravars

- [ ] **Step 3: Consolidate VM config routes into `app/routes/vm_config.py`**

- [ ] **Step 4: Consolidate snapshot routes into `app/routes/snapshots.py`**

- [ ] **Step 5: Consolidate firewall routes into `app/routes/firewall.py`**

- [ ] **Step 6: Consolidate network routes into `app/routes/network.py`**

- [ ] **Step 7: Consolidate storage routes into `app/routes/storage.py`**

- [ ] **Step 8: Consolidate bundle/scenario routes into `app/routes/bundles.py`**

**IMPORTANT:** Bundle routes have THREE distinct patterns — do NOT use the `_run_proxmox_action()` helper for these:

**Pattern A — Dynamic runner routes** (from `admin_run.py` → `actions_run.py`, `scenarios_run.py`):
- `/v0/admin/run/bundles/{bundles_name}/run` — uses path param, calls `utils.resolve_bundles_playbook()`
- `/v0/admin/run/scenarios/{scenario_name}/run` — uses path param, calls `utils.resolve_scenarios_playbook()`
- These use `Request_DebugPing` schema (not Proxmox schemas)
- They do NOT extract action results — just return raw logs

**Pattern B — Core Ubuntu bundle routes** (from `admin_run_bundles_core.py` → `v0/admin/bundles/core/linux/ubuntu/`):
- Routes like `/v0/admin/run/bundles/core/linux/ubuntu/install/docker`
- Each has a hardcoded playbook path resolved via `utils.resolve_bundles_playbook("core/linux/ubuntu/install/docker", "public_github")`
- Standard request → run_playbook_core → response pattern

**Pattern C — Core Proxmox multi-step bundle routes** (from `admin_run_bundles_core.py` → `v0/admin/bundles/core/proxmox/`):
- Routes like `/v0/admin/run/bundles/core/proxmox/configure/default/create-vms-admin`
- These iterate over `req.vms` dict and call `run_playbook_core()` MULTIPLE times per request
- Some call `init.yml` first, then `main.yml` for each VM
- Bundle schemas have nested per-VM models (e.g., `vms` dict with `vm_id`, `vm_ip`, `vm_description`)

Read every file under `app/routes/v0/admin/bundles/core/proxmox/` to capture the multi-step patterns before consolidating.

Also create `app/routes/runner.py` for the dynamic Pattern A routes, since they're architecturally different.

- [ ] **Step 9: Consolidate debug routes into `app/routes/debug.py`**

- [ ] **Step 10: Rewrite `app/routes/__init__.py` with new imports**

```python
# app/routes/__init__.py
from fastapi import APIRouter

from app.routes.vms import router as vms_router
from app.routes.vm_config import router as vm_config_router
from app.routes.snapshots import router as snapshots_router
from app.routes.firewall import router as firewall_router
from app.routes.network import router as network_router
from app.routes.storage import router as storage_router
from app.routes.bundles import router as bundles_router
from app.routes.runner import router as runner_router     # Dynamic {name}/run routes
from app.routes.debug import router as debug_router

router = APIRouter()

router.include_router(debug_router)
router.include_router(bundles_router)
router.include_router(runner_router)
router.include_router(vms_router)
router.include_router(vm_config_router)
router.include_router(snapshots_router)
router.include_router(firewall_router)
router.include_router(network_router)
router.include_router(storage_router)
```

- [ ] **Step 11: Run route registration test**

Run: `python -m pytest tests/test_routes_registered.py -v`
Expected: PASS — all routes still present

- [ ] **Step 12: Commit**

```bash
git add app/routes/ tests/test_routes_registered.py
git commit -m "refactor: consolidate 86 route files into 9 domain modules (#53)"
```

### Task 1.7: Rewrite `app/main.py` — Application Factory

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_app_factory.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_app_factory.py
from app.main import create_app


def test_create_app_returns_fastapi_instance():
    app = create_app()
    assert app.title == "CR42 - API"
    assert app.version == "v0.1"


def test_create_app_has_cors_middleware():
    app = create_app()
    middleware_classes = [m.cls.__name__ for m in app.user_middleware]
    assert "CORSMiddleware" in middleware_classes
```

- [ ] **Step 2: Run test, verify it fails**

Run: `python -m pytest tests/test_app_factory.py -v`
Expected: FAIL

- [ ] **Step 3: Rewrite main.py with application factory**

```python
# app/main.py
"""FastAPI application factory for the Range42 Backend API."""

import logging
import os
import shutil
import stat
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.exceptions import validation_exception_handler
from app.core.runner import vault_manager  # Module-level instance shared with routes
from app.routes import router as api_router
from app.routes.ws_status import router as ws_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage vault password lifecycle: setup on startup, cleanup on shutdown."""
    tmp_dir: Path | None = None

    vault_password_file = settings.vault_password_file
    vault_password = settings.vault_password

    if vault_password_file:
        p = Path(vault_password_file)
        if not p.exists():
            raise RuntimeError(f"Vault password file not found: {p}")
        vault_manager.set_vault_path(p)
        logger.info("Using VAULT_PASSWORD_FILE=%s", p)

    elif vault_password:
        tmp_dir = Path(tempfile.mkdtemp(prefix="vault-"))
        f = tmp_dir / "vault_pass.txt"
        f.write_text(vault_password)
        os.chmod(f, stat.S_IRUSR | stat.S_IWUSR)
        vault_manager.set_vault_path(f)
        logger.info("Using VAULT_PASSWORD (temp file)")

    else:
        logger.warning("No vault password provided")

    try:
        yield
    finally:
        if tmp_dir and tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)


def create_app() -> FastAPI:
    """Application factory. Creates and configures the FastAPI application."""
    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origin_regex=settings.cors_origin_regex,
            allow_credentials=True,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "Accept", "Authorization"],
            max_age=600,
        )
    ]

    app = FastAPI(
        title="CR42 - API",
        lifespan=lifespan,
        docs_url="/docs/swagger",
        redoc_url="/docs/redoc",
        openapi_url="/docs/openapi.json",
        version="v0.1",
        license_info={"name": "GPLv3"},
        contact={"email": "info@digisquad.com"},
        middleware=middleware,
    )

    # Register validation error handler (matches deployer-ui expected format)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    # Include routers
    app.include_router(api_router)
    app.include_router(ws_router)

    return app


# Module-level app instance for uvicorn
app = create_app()
```

- [ ] **Step 4: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_app_factory.py
git commit -m "refactor: rewrite main.py with application factory pattern (#54)"
```

### Task 1.8: Clean Up — Remove Old Files

**Files:**
- Remove: `app/routes/v0/` (entire directory)
- Remove: `app/routes/admin_proxmox.py`
- Remove: `app/routes/admin_run.py`
- Remove: `app/routes/admin_run_bundles_core.py`
- Remove: `app/routes/admin_debug.py`
- Remove: `app/schemas/proxmox/` (entire directory)
- Remove: `app/schemas/bundles/` (entire directory)
- Remove: `app/schemas/debug/` (keep `app/schemas/debug.py` — the new consolidated file)
- Remove: `app/vault/` (replaced by `app/core/vault.py`)
- Remove: `app/runner.py` (replaced by `app/core/runner.py`)
- Remove: `app/extract_actions.py` (replaced by `app/core/extractor.py`)

- [ ] **Step 1: Run all tests BEFORE deleting anything**

Run: `python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 2: Delete old route files**

```bash
rm -rf app/routes/v0/
rm -f app/routes/admin_proxmox.py app/routes/admin_run.py app/routes/admin_run_bundles_core.py app/routes/admin_debug.py
```

- [ ] **Step 3: Delete old schema files**

```bash
rm -rf app/schemas/proxmox/ app/schemas/bundles/ app/schemas/debug/
```

- [ ] **Step 4: Delete old vault and runner modules**

```bash
rm -rf app/vault/
rm -f app/runner.py app/extract_actions.py
```

- [ ] **Step 5: Run all tests AFTER deleting**

Run: `python -m pytest tests/ -v`
Expected: ALL PASS — if any test fails, a stale import remains; fix it before proceeding

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore: remove old nested files replaced by consolidated modules (#53)"
```

### Task 1.9: Fix Utils Logger Bug

**Files:**
- Modify: `app/utils/checks_playbooks.py:2`
- Modify: `app/utils/checks_inventory.py` (if same bug exists)

- [ ] **Step 1: Fix `from venv import logger` (line 2 of checks_playbooks.py)**

This is a bug — `venv.logger` is Python's virtual environment module, not a logging logger.

Change:
```python
from venv import logger
```
To:
```python
import logging
logger = logging.getLogger(__name__)
```

- [ ] **Step 2: Check `checks_inventory.py` for the same bug**

Read the file and apply the same fix if present.

- [ ] **Step 3: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add app/utils/
git commit -m "fix: replace broken venv.logger import with proper logging (#54)"
```

---

## Phase 2: Code Quality Refactor (#54)

### Task 2.1: Replace Print Statements with Structured Logging

**Files:**
- Modify: All files in `app/` that use `print()`
- Test: Manual verification via log output

- [ ] **Step 1: Find all print statements**

Run: `grep -rn "print(" app/ --include="*.py" | grep -v "__pycache__"`

- [ ] **Step 2: Replace each print() with appropriate logger call**

Pattern:
- `print(":: lifespan :: ...")` → `logger.info("...")`
- `print(":: err - ...")` → `logger.error("...")`
- `print(f":: work done :: {tmp_dir}")` → `logger.debug("Runner temp dir: %s", tmp_dir)`
- `print(":: REQUEST ::", ...)` → `logger.debug("Request: %s", ...)`
- Debug-only prints → `logger.debug()`

- [ ] **Step 3: Remove `debug = 0` / `debug = 1` flags from all route files**

These are no longer needed since logging is controlled by log level.

- [ ] **Step 4: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/
git commit -m "refactor: replace print statements with structured logging (#54)"
```

### Task 2.2: Add Type Hints to Core Modules

**Files:**
- Modify: `app/core/runner.py`
- Modify: `app/utils/checks_playbooks.py`
- Modify: `app/utils/checks_inventory.py`
- Modify: `app/utils/vm_id_name_resolver.py`

- [ ] **Step 1: Add type hints to all function signatures in utils/**

Each function should have full parameter types and return types.

- [ ] **Step 2: Run tests**

Run: `python -m pytest tests/ -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add app/
git commit -m "refactor: add type hints to core and utility modules (#54)"
```

### Task 2.3: Fix Dead Code in Runner

**Files:**
- Already handled in Task 1.3 (new runner doesn't have the bugs)
- Verify: No duplicate return statements, temp dir cleanup enabled

- [ ] **Step 1: Verify app/core/runner.py has no dead code**

Check that:
- `shutil.rmtree(tmp_dir)` is in the `finally` block (not commented out)
- No duplicate return statements
- No commented-out code blocks

- [ ] **Step 2: Commit (if changes needed)**

```bash
git add app/core/runner.py
git commit -m "fix: ensure temp dir cleanup and remove dead code in runner (#54)"
```

### Task 2.4: Create `start.sh` Template with Portable Env Vars

**Files:**
- Modify: `start.sh`
- Create: `.env.example`

- [ ] **Step 1: Create `.env.example`**

```bash
# .env.example — Copy to .env and fill in values
# Required
PROJECT_ROOT_DIR=           # Path to this project root
VAULT_PASSWORD_FILE=        # Path to vault password file
# OR
# VAULT_PASSWORD=           # Vault password string (alternative to file)

# Playbook sources
API_BACKEND_WWWAPP_PLAYBOOKS_DIR=     # Local playbooks directory (usually same as PROJECT_ROOT_DIR)
API_BACKEND_PUBLIC_PLAYBOOKS_DIR=     # External playbooks repo path
API_BACKEND_INVENTORY_DIR=            # Ansible inventory directory
API_BACKEND_VAULT_FILE=               # Vault-encrypted variables file

# Optional
CORS_ORIGIN_REGEX=          # Custom CORS regex (default: localhost only)
HOST=0.0.0.0                # Server bind address
PORT=8000                   # Server port
DEBUG=false                 # Enable debug mode (verbose 422 errors)
```

- [ ] **Step 2: Update `start.sh` to use `.env` file**

```bash
#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(realpath "$(dirname "${BASH_SOURCE[0]}")")"
export PROJECT_ROOT_DIR="$PROJECT_ROOT"

# Load .env file if it exists
if [ -f "$PROJECT_ROOT/.env" ]; then
    set -a
    source "$PROJECT_ROOT/.env"
    set +a
fi

# Install Ansible collections if needed
if [ ! -d "$HOME/.ansible/collections/ansible_collections/community/general" ]; then
    echo ":: Installing Ansible collections..."
    ansible-galaxy collection install -r requirements.yml -p ~/.ansible/collections
fi

# Set defaults for required vars
export API_BACKEND_WWWAPP_PLAYBOOKS_DIR="${API_BACKEND_WWWAPP_PLAYBOOKS_DIR:-$PROJECT_ROOT_DIR/}"
export API_BACKEND_INVENTORY_DIR="${API_BACKEND_INVENTORY_DIR:-$PROJECT_ROOT_DIR/inventory/}"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

cd "$PROJECT_ROOT_DIR" || exit
export PYTHONPATH="$PROJECT_ROOT:${PYTHONPATH:-}"

echo ":: start :: app.main:app - $PROJECT_ROOT_DIR"
exec uvicorn app.main:app \
    --host "$HOST" \
    --port "$PORT" \
    --log-level info \
    --reload
```

- [ ] **Step 3: Add `.env` to `.gitignore`**

Append to `.gitignore`:
```
.env
```

- [ ] **Step 4: Commit**

```bash
git add start.sh .env.example .gitignore
git commit -m "refactor: make start.sh portable with .env support (#54)"
```

---

## Phase 3: Docker Support (#51)

### Task 3.1: Create Dockerfile

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`

- [ ] **Step 1: Create `.dockerignore`**

```
.git
.gitignore
.env
__pycache__
*.pyc
.venv
venv
*.egg-info
.pytest_cache
docs/
curl_utils/
*.md
!requirements.txt
!requirements.yml
```

- [ ] **Step 2: Create Dockerfile**

```dockerfile
# Dockerfile
FROM python:3.12-slim AS base

# Install system deps for ansible and ssh
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-client \
    sshpass \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Ansible collections
COPY requirements.yml .
RUN ansible-galaxy collection install -r requirements.yml -p /usr/share/ansible/collections

# Copy application
COPY app/ app/
COPY playbooks/ playbooks/
COPY inventory/ inventory/
COPY start.sh .

# Set env defaults
ENV PROJECT_ROOT_DIR=/app
ENV API_BACKEND_WWWAPP_PLAYBOOKS_DIR=/app/
ENV API_BACKEND_INVENTORY_DIR=/app/inventory/
ENV HOST=0.0.0.0
ENV PORT=8000
ENV PYTHONPATH=/app

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/docs/openapi.json').raise_for_status()"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
```

- [ ] **Step 3: Build the image to verify**

Run: `cd /home/ppa/projects/range42-base/range42-backend-api && docker build -t range42-backend-api:dev .`
Expected: Build completes without error

- [ ] **Step 4: Commit**

```bash
git add Dockerfile .dockerignore
git commit -m "feat(docker): add Dockerfile for containerized deployment (#51)"
```

### Task 3.2: Create docker-compose.yml

**Files:**
- Create: `docker-compose.yml`

- [ ] **Step 1: Create docker-compose.yml**

```yaml
# docker-compose.yml
services:
  api:
    build: .
    ports:
      - "${PORT:-8000}:8000"
    volumes:
      # Mount local playbooks and inventory for development
      - ./app:/app/app:ro
      - ./playbooks:/app/playbooks:ro
      - ./inventory:/app/inventory:ro
      # Mount external playbooks repo (optional)
      - ${API_BACKEND_PUBLIC_PLAYBOOKS_DIR:-./playbooks}:/external-playbooks:ro
      # Mount vault password file (optional)
      - ${VAULT_PASSWORD_FILE:-/dev/null}:/run/secrets/vault_pass:ro
    environment:
      - PROJECT_ROOT_DIR=/app
      - API_BACKEND_WWWAPP_PLAYBOOKS_DIR=/app/
      - API_BACKEND_PUBLIC_PLAYBOOKS_DIR=/external-playbooks/
      - API_BACKEND_INVENTORY_DIR=/app/inventory/
      - API_BACKEND_VAULT_FILE=${API_BACKEND_VAULT_FILE:-}
      - VAULT_PASSWORD_FILE=/run/secrets/vault_pass
      - VAULT_PASSWORD=${VAULT_PASSWORD:-}
      - CORS_ORIGIN_REGEX=${CORS_ORIGIN_REGEX:-}
      - DEBUG=${DEBUG:-false}
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import httpx; httpx.get('http://localhost:8000/docs/openapi.json').raise_for_status()"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
```

- [ ] **Step 2: Verify compose starts**

Run: `cd /home/ppa/projects/range42-base/range42-backend-api && docker compose up --build -d && docker compose logs api && docker compose down`
Expected: Container starts, logs show "Uvicorn running on..."

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(docker): add docker-compose.yml for local development (#51)"
```

---

## Phase 4: Documentation (#52)

### Task 4.1: Rewrite README.md

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Read current README**

Read `README.md` to understand what's already documented.

- [ ] **Step 2: Rewrite README with comprehensive documentation**

The README should cover:

1. **Project overview** — What this API does, where it sits in Range42 architecture
2. **Quick start** — 3 options: Docker, docker-compose, manual setup
3. **Configuration** — Table of all environment variables with descriptions and defaults
4. **API documentation** — How to access Swagger/ReDoc, link to OpenAPI spec
5. **Project structure** — Updated directory tree reflecting new structure
6. **Development** — How to set up dev environment, run tests, lint
7. **Architecture** — Request flow diagram, key modules explained
8. **Deployment** — Production considerations (Kong gateway, SSL, scaling)
9. **Contributing** — Commit conventions, branch naming, PR guidelines

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: comprehensive README with setup, architecture, and API guide (#52)"
```

### Task 4.2: Add Sphinx-Style Docstrings to All Modules

**Files:**
- Modify: All `.py` files in `app/core/`, `app/routes/`, `app/schemas/`, `app/utils/`

- [ ] **Step 1: Document `app/core/` modules**

Add Sphinx-style docstrings (`:param:`, `:type:`, `:returns:`, `:rtype:`, `:raises:`) to every public function and class in:
- `app/core/config.py` — `Settings` class and its properties
- `app/core/runner.py` — `run_playbook_core()`, `build_logs()`, `_setup_temp_dir()`, `_build_cmdline()`, `_build_envvars()`
- `app/core/extractor.py` — `extract_action_results()`
- `app/core/vault.py` — `VaultManager` class
- `app/core/exceptions.py` — `validation_exception_handler()`, `make_validation_error_detail()`

Example format:
```python
def run_playbook_core(
    playbook: Path,
    inventory: Path,
    limit: str | None = None,
    tags: str | None = None,
    cmdline: str | None = None,
    extravars: dict | None = None,
    quiet: bool = False,
) -> tuple[int, list[dict], str, str]:
    """Execute an Ansible playbook via ansible-runner and return structured results.

    Creates a temporary execution directory, copies the playbook tree and inventory,
    sets Ansible environment variables, runs the playbook, and cleans up.

    :param playbook: Absolute path to the playbook YAML file.
    :type playbook: Path
    :param inventory: Absolute path to the inventory file.
    :type inventory: Path
    :param limit: Ansible ``--limit`` pattern to target specific hosts.
    :type limit: str or None
    :param tags: Comma-separated Ansible tags to select tasks.
    :type tags: str or None
    :param cmdline: Additional raw CLI arguments for ``ansible-playbook``.
    :type cmdline: str or None
    :param extravars: Extra variables passed to the playbook as ``-e`` args.
    :type extravars: dict or None
    :param quiet: Suppress ansible-runner stdout if True.
    :type quiet: bool
    :returns: Tuple of (return_code, events, log_plain, log_ansi).
    :rtype: tuple[int, list[dict], str, str]
    :raises FileNotFoundError: If playbook or inventory file does not exist.
    """
```

- [ ] **Step 2: Document `app/routes/` modules**

Each route module gets a module-level docstring describing its domain and listing all endpoints:
```python
"""VM lifecycle routes for the Proxmox API.

Endpoints:
    POST /v0/admin/proxmox/vms/list
    POST /v0/admin/proxmox/vms/list_usage
    POST /v0/admin/proxmox/vms/vm_id/start
    POST /v0/admin/proxmox/vms/vm_id/stop
    ...
"""
```

Each route handler function gets a one-line summary + `:param:` for the request model:
```python
@router.post(path="/start", ...)
def proxmox_vms_vm_id_start(req: VmActionRequest):
    """Start a specific virtual machine on Proxmox.

    :param req: Request body with ``proxmox_node`` and ``vm_id``.
    :type req: VmActionRequest
    :returns: JSON with ``rc`` (return code) and ``result`` or ``log_multiline``.
    :rtype: JSONResponse
    """
```

- [ ] **Step 3: Document `app/schemas/` modules**

Each Pydantic model class gets a docstring describing its purpose and example usage:
```python
class VmActionRequest(ProxmoxBaseRequest):
    """Request body for single-VM lifecycle operations (start, stop, pause, resume).

    :param vm_id: Proxmox VM ID (numeric string).
    :type vm_id: str

    Example::

        {"proxmox_node": "pve01", "vm_id": "100", "as_json": true}
    """
```

- [ ] **Step 4: Document `app/utils/` modules**

Add docstrings to `checks_playbooks.py`, `checks_inventory.py`, `text_cleaner.py`, `vm_id_name_resolver.py`.

- [ ] **Step 5: Run tests to verify docstrings don't break anything**

Run: `python -m pytest tests/ -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/
git commit -m "docs: add Sphinx-style docstrings to all modules (#52)"
```

### Task 4.3: Update curl_utils Scripts (if paths changed)

**Files:**
- Review: All files in `curl_utils/`

- [ ] **Step 1: Verify curl scripts still work**

The API paths haven't changed (only the Python file organization), so curl scripts should still work. Verify by reading a few scripts and confirming the endpoint URLs match.

- [ ] **Step 2: If no changes needed, skip this task**

---

## Phase 5: Final Verification

### Task 5.1: Full Test Suite Run

- [ ] **Step 1: Run all tests**

Run: `cd /home/ppa/projects/range42-base/range42-backend-api && python -m pytest tests/ -v --tb=short`
Expected: ALL PASS

- [ ] **Step 2: Run the app and verify OpenAPI schema**

Run: Start the app with `./start.sh`, then:
```bash
curl -s http://localhost:8000/docs/openapi.json | python -m json.tool | head -20
```
Expected: Valid OpenAPI JSON with all routes

- [ ] **Step 3: Compare old vs new OpenAPI schemas**

If you saved the old schema before restructuring, diff them:
```bash
diff <(curl -s http://localhost:8000/docs/openapi.json | python -m json.tool) old-openapi.json
```
Expected: Paths identical (only schema descriptions may differ)

- [ ] **Step 4: Docker build and run test**

```bash
docker build -t range42-backend-api:test . && docker run --rm -p 8000:8000 range42-backend-api:test &
sleep 5
curl -s http://localhost:8000/docs/openapi.json | python -m json.tool | head -5
docker stop $(docker ps -q --filter ancestor=range42-backend-api:test)
```

### Task 5.2: Final Commit and Issue Closure

- [ ] **Step 1: Review all changes**

```bash
git log --oneline --since="today"
git diff --stat main
```

- [ ] **Step 2: Verify file count reduction**

```bash
find app/ -name "*.py" | wc -l
```
Expected: ~25 files (down from ~150)

- [ ] **Step 3: Update GitHub issues with references**

Each commit already references the issue numbers. The PR will close them.

---

## Summary of Changes

| Metric | Before | After |
|--------|--------|-------|
| Python files in `app/` | ~150 | ~25 |
| Route files | 86 | 10 (vms, vm_config, snapshots, firewall, network, storage, bundles, runner, debug, ws_status) |
| Schema files | 52 | 9 (base, vms, vm_config, snapshots, firewall, network, storage, bundles, debug) |
| Max directory depth | 7 levels | 3 levels |
| Test files | 0 | 7+ |
| Dockerfile | none | multi-stage |
| docker-compose | none | full dev setup |
| Config approach | hardcoded in start.sh | .env + Settings class |
| Logging | print() + debug flags | structured logging |
| Global state | vault._VAULT_PASS_PATH | VaultManager instance |
| Temp dir cleanup | disabled (memory leak) | enabled in finally block |
| Dead code | duplicate returns in runner | removed |
| Logger bug | `from venv import logger` | `logging.getLogger()` |

## Risk Mitigation

1. **API compatibility** — Smoke tests verify every route exists before AND after each change
2. **Incremental approach** — Old files kept with re-exports during migration, deleted only after tests pass
3. **Phase ordering** — Structure first (safe), refactor second (depends on structure), Docker third (wraps result), docs last (describes final state)
4. **No functionality changes** — This is purely structural and quality work; no new features, no behavior changes
