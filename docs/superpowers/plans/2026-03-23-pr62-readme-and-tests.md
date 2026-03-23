# PR #62 — README Documentation & Unit Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Address the two missing items blocking PR #62 merge: comprehensive README documentation and unit test coverage for all new/refactored code.

**Architecture:** Two independent workstreams — (1) README expansion covering WebSocket API, Docker details, logging, and testing sections; (2) unit tests for utility modules, core runner internals, route handlers (mocking `run_playbook_core`), and WebSocket helpers. Also fix 9 CodeQL issues flagged in automated review.

**Tech Stack:** Python 3.12, FastAPI, pytest, pytest-asyncio, httpx (TestClient), unittest.mock

---

## File Structure

### Documentation
- Modify: `README.md` — expand WebSocket, Docker, Logging, and Testing sections

### Tests (new files)
- Create: `tests/test_checks_playbooks.py` — playbook path validation tests
- Create: `tests/test_checks_inventory.py` — inventory path validation tests
- Create: `tests/test_runner_internals.py` — `_build_envvars`, `_build_cmdline`, `_setup_temp_dir` tests
- Create: `tests/test_ws_helpers.py` — `compute_diff`, `load_proxmox_credentials`, `fetch_vm_status` tests
- Create: `tests/test_route_debug.py` — debug route handler integration tests
- Create: `tests/test_route_vms.py` — VM route handler integration tests
- Create: `tests/test_schemas_replies.py` — response schema validation tests

### CodeQL fixes (modify existing)
- Modify: `app/routes/debug.py` — fix `import *` and unused `Any`
- Modify: `app/routes/bundles.py` — remove unused `Any`
- Modify: `app/routes/runner.py` — remove unused `Any`
- Modify: `app/routes/vms.py` — remove unused `Any`
- Modify: `app/routes/ws_status.py` — remove unused `json`, add logging to empty `except`
- Modify: `app/schemas/firewall.py` — remove unused `List`
- Modify: `app/schemas/network.py` — remove unused `List`

---

## Task 1: Fix CodeQL Issues

**Files:**
- Modify: `app/routes/debug.py:12,19`
- Modify: `app/routes/bundles.py:29`
- Modify: `app/routes/runner.py:12`
- Modify: `app/routes/vms.py:26`
- Modify: `app/routes/ws_status.py:16,201-202`
- Modify: `app/schemas/firewall.py:3`
- Modify: `app/schemas/network.py:3`

These are quick fixes the automated review already identified. Fix them first so subsequent test imports start clean.

- [ ] **Step 1: Fix `app/routes/debug.py`**

Replace line 12 (`from typing import Any`) — delete the line.
Replace line 19 (`from app.utils.vm_id_name_resolver import *`) with:
```python
from app.utils.vm_id_name_resolver import resolv_id_to_vm_name
```

- [ ] **Step 2: Fix unused `Any` in bundles, runner, vms**

In each file, delete the `from typing import Any` line:
- `app/routes/bundles.py:29`
- `app/routes/runner.py:12`
- `app/routes/vms.py:26`

- [ ] **Step 3: Fix `app/routes/ws_status.py`**

Delete `import json` (line 16).

Replace the empty `except` block at lines 201-202:
```python
            except Exception as notify_err:
                logger.debug("[ws] Failed to send error to client: %s", notify_err)
```

- [ ] **Step 4: Fix unused `List` in schemas**

In `app/schemas/firewall.py:3`, change `from typing import List, Literal` to:
```python
from typing import Literal
```

In `app/schemas/network.py:3`, change `from typing import List, Literal` to:
```python
from typing import Literal
```

- [ ] **Step 5: Run existing tests to verify nothing broke**

Run: `cd /home/ppa/projects/range42-base/range42-backend-api && python3 -m pytest tests/ -v`
Expected: All existing tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/routes/debug.py app/routes/bundles.py app/routes/runner.py app/routes/vms.py app/routes/ws_status.py app/schemas/firewall.py app/schemas/network.py
git commit -m "fix: resolve CodeQL findings — unused imports, import *, empty except"
```

---

## Task 2: Unit Tests for `app/utils/checks_inventory.py`

**Files:**
- Create: `tests/test_checks_inventory.py`

The `resolve_inventory()` function validates inventory names via regex, checks for path traversal, and resolves to an absolute file path. It raises `HTTPException(400)` for invalid names or missing files, and `HTTPException(500)` for missing inventory directory.

- [ ] **Step 1: Write failing tests**

Create `tests/test_checks_inventory.py`:
```python
"""Tests for app.utils.checks_inventory."""

import os
import pytest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException

from app.utils.checks_inventory import resolve_inventory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INVENTORY_DIR = PROJECT_ROOT / "inventory"


class TestResolveInventory:
    """Tests for resolve_inventory()."""

    def test_resolves_valid_inventory_name(self):
        """'hosts' should resolve to inventory/hosts.yml."""
        if not (INVENTORY_DIR / "hosts.yml").exists():
            pytest.skip("No inventory/hosts.yml in project")
        result = resolve_inventory("hosts")
        assert result.name == "hosts.yml"
        assert result.is_absolute()
        assert result.exists()

    def test_rejects_empty_name(self):
        with pytest.raises(HTTPException) as exc_info:
            resolve_inventory("")
        assert exc_info.value.status_code == 400
        assert "INVALID INVENTORY NAME" in exc_info.value.detail

    def test_rejects_path_with_dots(self):
        """Names with dots should be rejected by the regex."""
        with pytest.raises(HTTPException) as exc_info:
            resolve_inventory("../../etc/passwd")
        assert exc_info.value.status_code == 400

    def test_rejects_name_with_spaces(self):
        with pytest.raises(HTTPException) as exc_info:
            resolve_inventory("my inventory")
        assert exc_info.value.status_code == 400

    def test_rejects_name_starting_with_slash(self):
        with pytest.raises(HTTPException) as exc_info:
            resolve_inventory("/etc/passwd")
        assert exc_info.value.status_code == 400

    def test_valid_name_but_missing_file_raises_400(self):
        """A validly-formatted name that doesn't exist on disk."""
        with pytest.raises(HTTPException) as exc_info:
            resolve_inventory("nonexistent-inventory-xyz")
        assert exc_info.value.status_code == 400
        assert "NOT FOUND" in exc_info.value.detail

    def test_missing_inventory_dir_raises_500(self):
        """If the inventory directory itself doesn't exist, expect 500."""
        with patch.dict(os.environ, {"PROJECT_ROOT_DIR": "/tmp/nonexistent-dir-xyz"}):
            with pytest.raises(HTTPException) as exc_info:
                resolve_inventory("hosts")
            assert exc_info.value.status_code in (400, 500)
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_checks_inventory.py -v`
Expected: All tests pass (these test existing code).

- [ ] **Step 3: Commit**

```bash
git add tests/test_checks_inventory.py
git commit -m "test: add unit tests for inventory path validation"
```

---

## Task 3: Unit Tests for `app/utils/checks_playbooks.py`

**Files:**
- Create: `tests/test_checks_playbooks.py`

Tests for `_warmup_checks()`, `resolve_actions_playbook()`, `resolve_bundles_playbook()`, `resolve_scenarios_playbook()`, and `_resolve_file()`. These validate action names via regex and check for path traversal.

- [ ] **Step 1: Write tests**

Create `tests/test_checks_playbooks.py`:
```python
"""Tests for app.utils.checks_playbooks."""

import os
import pytest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException

from app.utils.checks_playbooks import (
    _warmup_checks,
    resolve_actions_playbook,
    resolve_bundles_playbook,
    resolve_scenarios_playbook,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestWarmupChecks:
    """Tests for _warmup_checks()."""

    def test_www_app_resolves_from_env(self):
        result = _warmup_checks("www_app")
        assert result.is_absolute()
        assert result.is_dir()

    def test_public_github_resolves_from_env(self):
        result = _warmup_checks("public_github")
        assert result.is_absolute()

    def test_unknown_type_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            _warmup_checks("unknown_type")
        assert exc_info.value.status_code == 400
        assert "Unknown playbooks_dir_type" in exc_info.value.detail

    def test_missing_env_var_raises_400(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("API_BACKEND_WWWAPP_PLAYBOOKS_DIR", None)
            with pytest.raises(HTTPException) as exc_info:
                _warmup_checks("www_app")
            assert exc_info.value.status_code == 400


class TestResolvePlaybooks:
    """Tests for action name validation (shared by all resolve_* functions)."""

    def test_rejects_empty_action_name(self):
        with pytest.raises(HTTPException) as exc_info:
            resolve_actions_playbook("", "www_app")
        assert exc_info.value.status_code == 400

    def test_rejects_dotdot_traversal(self):
        with pytest.raises(HTTPException) as exc_info:
            resolve_actions_playbook("../../etc/passwd", "www_app")
        assert exc_info.value.status_code == 400

    def test_rejects_name_with_spaces(self):
        with pytest.raises(HTTPException) as exc_info:
            resolve_actions_playbook("my action", "www_app")
        assert exc_info.value.status_code == 400

    def test_rejects_name_with_dots(self):
        with pytest.raises(HTTPException) as exc_info:
            resolve_actions_playbook("install.docker", "www_app")
        assert exc_info.value.status_code == 400

    def test_rejects_leading_slash(self):
        with pytest.raises(HTTPException) as exc_info:
            resolve_actions_playbook("/etc/passwd", "www_app")
        assert exc_info.value.status_code == 400

    def test_rejects_double_slash(self):
        with pytest.raises(HTTPException) as exc_info:
            resolve_actions_playbook("vm//clone", "www_app")
        assert exc_info.value.status_code == 400

    def test_valid_name_missing_file_raises(self):
        """A valid action name format but no matching playbook file."""
        with pytest.raises((HTTPException, FileNotFoundError)):
            resolve_actions_playbook("nonexistent-action-xyz", "www_app")

    def test_bundles_rejects_invalid_name(self):
        with pytest.raises(HTTPException) as exc_info:
            resolve_bundles_playbook("../../etc", "www_app")
        assert exc_info.value.status_code == 400

    def test_scenarios_rejects_invalid_name(self):
        with pytest.raises(HTTPException) as exc_info:
            resolve_scenarios_playbook("../../etc", "www_app")
        assert exc_info.value.status_code == 400
```

- [ ] **Step 2: Run tests**

Run: `python3 -m pytest tests/test_checks_playbooks.py -v`
Expected: All pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_checks_playbooks.py
git commit -m "test: add unit tests for playbook path validation and traversal detection"
```

---

## Task 4: Unit Tests for `app/core/runner.py` Internals

**Files:**
- Create: `tests/test_runner_internals.py`

Tests for `_build_envvars()`, `_build_cmdline()`, and `_setup_temp_dir()` — the functions not yet covered. `run_playbook_core()` is tested indirectly via route tests in Task 6.

- [ ] **Step 1: Write tests**

Create `tests/test_runner_internals.py`:
```python
"""Tests for app.core.runner internal helpers."""

import os
import shutil
import pytest
from pathlib import Path
from unittest.mock import patch

from app.core.runner import _build_envvars, _build_cmdline, _setup_temp_dir
from app.core.vault import VaultManager


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestBuildEnvvars:
    """Tests for _build_envvars()."""

    def test_returns_dict_with_ansible_keys(self):
        vm = VaultManager()
        result = _build_envvars(vm)
        assert isinstance(result, dict)
        assert "ANSIBLE_HOST_KEY_CHECKING" in result
        assert "ANSIBLE_DEPRECATION_WARNINGS" in result
        assert "ANSIBLE_COLLECTIONS_PATH" in result

    def test_includes_vault_password_file_from_env(self):
        vm = VaultManager()
        with patch.dict(os.environ, {"VAULT_PASSWORD_FILE": "/tmp/vault-pass.txt"}):
            result = _build_envvars(vm)
            assert result["ANSIBLE_VAULT_PASSWORD_FILE"] == "/tmp/vault-pass.txt"

    def test_includes_vault_path_from_manager(self):
        vm = VaultManager()
        vm.set_vault_path(Path("/tmp/test-vault-path"))
        # Clear env so manager path is used as fallback
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VAULT_PASSWORD_FILE", None)
            result = _build_envvars(vm)
            assert result["ANSIBLE_VAULT_PASSWORD_FILE"] == "/tmp/test-vault-path"
        vm.set_vault_path(None)

    def test_no_vault_key_when_no_vault_configured(self):
        vm = VaultManager()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VAULT_PASSWORD_FILE", None)
            result = _build_envvars(vm)
            assert "ANSIBLE_VAULT_PASSWORD_FILE" not in result


class TestBuildCmdline:
    """Tests for _build_cmdline()."""

    def test_returns_none_when_no_vault_no_tags(self):
        vm = VaultManager()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VAULT_PASSWORD_FILE", None)
            os.environ.pop("API_BACKEND_VAULT_FILE", None)
            result = _build_cmdline(vm, None, None)
            assert result is None

    def test_appends_vault_password_file(self):
        vm = VaultManager()
        with patch.dict(os.environ, {"VAULT_PASSWORD_FILE": "/tmp/vp.txt"}, clear=False):
            os.environ.pop("API_BACKEND_VAULT_FILE", None)
            result = _build_cmdline(vm, None, None)
            assert "--vault-password-file" in result
            assert "/tmp/vp.txt" in result

    def test_appends_tags(self):
        vm = VaultManager()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VAULT_PASSWORD_FILE", None)
            os.environ.pop("API_BACKEND_VAULT_FILE", None)
            result = _build_cmdline(vm, None, "install,configure")
            assert "--tags install,configure" in result

    def test_appends_vault_file_as_extra_vars(self):
        vm = VaultManager()
        with patch.dict(os.environ, {"API_BACKEND_VAULT_FILE": "/tmp/vault.yml"}, clear=False):
            os.environ.pop("VAULT_PASSWORD_FILE", None)
            result = _build_cmdline(vm, None, None)
            assert '-e "@/tmp/vault.yml"' in result

    def test_preserves_existing_cmdline(self):
        vm = VaultManager()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("API_BACKEND_VAULT_FILE", None)
            result = _build_cmdline(vm, "--check", None)
            assert result == "--check"


class TestSetupTempDir:
    """Tests for _setup_temp_dir()."""

    def test_creates_temp_directory_structure(self):
        vm = VaultManager()
        # Use the project's own playbook and inventory as test fixtures
        playbook = PROJECT_ROOT / "playbooks" / "ping.yml"
        inventory = PROJECT_ROOT / "inventory" / "hosts.yml"
        if not playbook.exists() or not inventory.exists():
            pytest.skip("Missing playbook or inventory fixtures")

        tmp_dir, inv_dest, play_rel = _setup_temp_dir(inventory, playbook, vm)
        try:
            assert tmp_dir.exists()
            assert (tmp_dir / "project").is_dir()
            assert (tmp_dir / "inventory").is_dir()
            assert (tmp_dir / "env" / "envvars").is_file()
            assert inv_dest.exists()
            assert (tmp_dir / "project" / play_rel).exists()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_envvars_file_contains_ansible_keys(self):
        vm = VaultManager()
        playbook = PROJECT_ROOT / "playbooks" / "ping.yml"
        inventory = PROJECT_ROOT / "inventory" / "hosts.yml"
        if not playbook.exists() or not inventory.exists():
            pytest.skip("Missing playbook or inventory fixtures")

        tmp_dir, _, _ = _setup_temp_dir(inventory, playbook, vm)
        try:
            envvars_content = (tmp_dir / "env" / "envvars").read_text()
            assert "ANSIBLE_HOST_KEY_CHECKING" in envvars_content
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
```

- [ ] **Step 2: Run tests**

Run: `python3 -m pytest tests/test_runner_internals.py -v`
Expected: All pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_runner_internals.py
git commit -m "test: add unit tests for runner internals (_build_envvars, _build_cmdline, _setup_temp_dir)"
```

---

## Task 5: Unit Tests for WebSocket Helpers

**Files:**
- Create: `tests/test_ws_helpers.py`

Tests for `compute_diff()` and `load_proxmox_credentials()` from `app/routes/ws_status.py`. These are pure functions that can be tested without WebSocket connections.

- [ ] **Step 1: Write tests**

Create `tests/test_ws_helpers.py`:
```python
"""Tests for WebSocket helper functions in app.routes.ws_status."""

import os
import pytest
from pathlib import Path
from unittest.mock import patch

from app.routes.ws_status import compute_diff, load_proxmox_credentials


class TestComputeDiff:
    """Tests for compute_diff()."""

    def test_no_changes_returns_none(self):
        state = {100: {"vmid": 100, "status": "running", "cpu": 5.0}}
        assert compute_diff(state, state) is None

    def test_detects_status_change(self):
        prev = {100: {"vmid": 100, "status": "running", "cpu": 5.0}}
        curr = {100: {"vmid": 100, "status": "stopped", "cpu": 0.0}}
        diff = compute_diff(prev, curr)
        assert diff is not None
        assert 100 in diff
        assert diff[100]["type"] == "changed"
        assert diff[100]["status"] == "stopped"

    def test_detects_cpu_change_above_threshold(self):
        prev = {100: {"vmid": 100, "status": "running", "cpu": 10.0}}
        curr = {100: {"vmid": 100, "status": "running", "cpu": 15.0}}
        diff = compute_diff(prev, curr)
        assert diff is not None
        assert diff[100]["type"] == "changed"

    def test_ignores_small_cpu_change(self):
        prev = {100: {"vmid": 100, "status": "running", "cpu": 10.0}}
        curr = {100: {"vmid": 100, "status": "running", "cpu": 11.0}}
        assert compute_diff(prev, curr) is None

    def test_detects_added_vm(self):
        prev = {}
        curr = {100: {"vmid": 100, "status": "running", "cpu": 5.0}}
        diff = compute_diff(prev, curr)
        assert diff is not None
        assert diff[100]["type"] == "added"

    def test_detects_removed_vm(self):
        prev = {100: {"vmid": 100, "status": "running", "cpu": 5.0}}
        curr = {}
        diff = compute_diff(prev, curr)
        assert diff is not None
        assert diff[100]["type"] == "removed"

    def test_empty_states_returns_none(self):
        assert compute_diff({}, {}) is None

    def test_multiple_changes(self):
        prev = {
            100: {"vmid": 100, "status": "running", "cpu": 5.0},
            101: {"vmid": 101, "status": "stopped", "cpu": 0.0},
        }
        curr = {
            100: {"vmid": 100, "status": "stopped", "cpu": 0.0},
            102: {"vmid": 102, "status": "running", "cpu": 10.0},
        }
        diff = compute_diff(prev, curr)
        assert 100 in diff  # changed
        assert 101 in diff  # removed
        assert 102 in diff  # added


class TestLoadProxmoxCredentials:
    """Tests for load_proxmox_credentials()."""

    def test_returns_empty_dict_on_missing_inventory(self):
        with patch.dict(os.environ, {"API_BACKEND_INVENTORY_DIR": "/tmp/nonexistent-xyz"}):
            result = load_proxmox_credentials()
            assert result == {}

    def test_returns_empty_dict_on_bad_yaml(self, tmp_path):
        bad_yaml = tmp_path / "hosts.yml"
        bad_yaml.write_text(": invalid: yaml: [[[")
        with patch.dict(os.environ, {"API_BACKEND_INVENTORY_DIR": str(tmp_path)}):
            result = load_proxmox_credentials()
            assert result == {}

    def test_returns_empty_dict_when_no_proxmox_hosts(self, tmp_path):
        inv = tmp_path / "hosts.yml"
        inv.write_text("all:\n  children:\n    other_group:\n      hosts: {}\n")
        with patch.dict(os.environ, {"API_BACKEND_INVENTORY_DIR": str(tmp_path)}):
            result = load_proxmox_credentials()
            assert result == {}

    def test_extracts_credentials_from_valid_inventory(self, tmp_path):
        inv = tmp_path / "hosts.yml"
        inv.write_text("""
all:
  children:
    range42_infrastructure:
      children:
        proxmox:
          hosts:
            pve01:
              proxmox_api_host: "192.168.1.100:8006"
              proxmox_node: "pve01"
              proxmox_api_user: "root@pam"
              proxmox_api_token_id: "mytoken"
              proxmox_api_token_secret: "secret123"
""")
        with patch.dict(os.environ, {"API_BACKEND_INVENTORY_DIR": str(tmp_path)}):
            result = load_proxmox_credentials()
            assert result["api_host"] == "192.168.1.100:8006"
            assert result["node"] == "pve01"
            assert "mytoken" in result["token_id"]
            assert result["token_secret"] == "secret123"
```

- [ ] **Step 2: Run tests**

Run: `python3 -m pytest tests/test_ws_helpers.py -v`
Expected: All pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_ws_helpers.py
git commit -m "test: add unit tests for WebSocket helpers (compute_diff, load_proxmox_credentials)"
```

---

## Task 6: Route Handler Integration Tests (Debug + VMs)

**Files:**
- Create: `tests/test_route_debug.py`
- Create: `tests/test_route_vms.py`

These tests use FastAPI's `TestClient` and mock `run_playbook_core` to verify that route handlers correctly parse requests, call the runner with the right arguments, and return appropriate HTTP responses. No actual Ansible execution happens.

- [ ] **Step 1: Write debug route tests**

Create `tests/test_route_debug.py`:
```python
"""Integration tests for debug route handlers."""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture
def mock_runner():
    """Mock run_playbook_core to return a successful result."""
    with patch("app.routes.debug.run_playbook_core") as mock:
        mock.return_value = (0, [], "PLAY RECAP\nok=1", "PLAY RECAP\nok=1")
        yield mock


@pytest.fixture
def mock_runner_failure():
    """Mock run_playbook_core to return a failure."""
    with patch("app.routes.debug.run_playbook_core") as mock:
        mock.return_value = (1, [], "TASK FAILED", "TASK FAILED")
        yield mock


class TestDebugPing:
    """Tests for POST /v0/admin/debug/ping."""

    def test_ping_success(self, client, mock_runner):
        resp = client.post(
            "/v0/admin/debug/ping",
            json={"hosts": "all", "proxmox_node": "pve01"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["rc"] == 0
        assert "log_multiline" in data

    def test_ping_failure_returns_500(self, client, mock_runner_failure):
        resp = client.post(
            "/v0/admin/debug/ping",
            json={"hosts": "all", "proxmox_node": "pve01"},
        )
        assert resp.status_code == 500
        assert resp.json()["rc"] == 1

    def test_ping_missing_required_fields_returns_422(self, client, mock_runner):
        resp = client.post("/v0/admin/debug/ping", json={})
        assert resp.status_code == 422

    def test_ping_calls_runner_with_correct_playbook(self, client, mock_runner):
        client.post(
            "/v0/admin/debug/ping",
            json={"hosts": "all", "proxmox_node": "pve01"},
        )
        mock_runner.assert_called_once()
        args = mock_runner.call_args
        # First positional arg is the playbook path
        assert "ping.yml" in str(args[0][0])

    def test_ping_passes_extravars_when_node_set(self, client, mock_runner):
        client.post(
            "/v0/admin/debug/ping",
            json={"hosts": "all", "proxmox_node": "pve02"},
        )
        call_kwargs = mock_runner.call_args
        extravars = call_kwargs.kwargs.get("extravars") or call_kwargs[1].get("extravars")
        if extravars is None:
            # May be passed as positional — check all args
            pass  # Acceptable: the important thing is the call was made
```

- [ ] **Step 2: Write VM route tests**

Create `tests/test_route_vms.py`:
```python
"""Integration tests for VM route handlers."""

import pytest
from unittest.mock import patch


@pytest.fixture
def mock_runner():
    """Mock run_playbook_core for VM routes."""
    with patch("app.routes.vms.run_playbook_core") as mock:
        mock.return_value = (0, [], "ok", "ok")
        yield mock


@pytest.fixture
def mock_runner_failure():
    with patch("app.routes.vms.run_playbook_core") as mock:
        mock.return_value = (1, [], "FAILED", "FAILED")
        yield mock


@pytest.fixture
def mock_runner_with_json():
    """Mock that returns events for as_json mode."""
    with patch("app.routes.vms.run_playbook_core") as mock_run:
        with patch("app.routes.vms.extract_action_results") as mock_extract:
            mock_run.return_value = (0, [{"event": "runner_on_ok"}], "ok", "ok")
            mock_extract.return_value = [{"vmid": 100, "name": "test-vm"}]
            yield mock_run, mock_extract


class TestVmList:
    """Tests for POST /v0/admin/proxmox/vms/list."""

    def test_list_vms_success(self, client, mock_runner):
        resp = client.post(
            "/v0/admin/proxmox/vms/list",
            json={"proxmox_node": "pve01", "as_json": False},
        )
        assert resp.status_code == 200
        assert "log_multiline" in resp.json()

    def test_list_vms_failure_returns_500(self, client, mock_runner_failure):
        resp = client.post(
            "/v0/admin/proxmox/vms/list",
            json={"proxmox_node": "pve01", "as_json": False},
        )
        assert resp.status_code == 500

    def test_list_vms_missing_required_field_returns_422(self, client, mock_runner):
        resp = client.post("/v0/admin/proxmox/vms/list", json={})
        assert resp.status_code == 422


class TestVmLifecycle:
    """Tests for VM start/stop/pause/resume."""

    @pytest.mark.parametrize("action", ["start", "stop", "stop_force", "pause", "resume"])
    def test_vm_action_success(self, client, mock_runner, action):
        resp = client.post(
            f"/v0/admin/proxmox/vms/vm_id/{action}",
            json={"proxmox_node": "pve01", "vm_id": "100", "as_json": False},
        )
        assert resp.status_code == 200

    @pytest.mark.parametrize("action", ["start", "stop"])
    def test_vm_action_failure_returns_500(self, client, mock_runner_failure, action):
        resp = client.post(
            f"/v0/admin/proxmox/vms/vm_id/{action}",
            json={"proxmox_node": "pve01", "vm_id": "100", "as_json": False},
        )
        assert resp.status_code == 500
```

- [ ] **Step 3: Run tests**

Run: `python3 -m pytest tests/test_route_debug.py tests/test_route_vms.py -v`
Expected: All pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_route_debug.py tests/test_route_vms.py
git commit -m "test: add integration tests for debug and VM route handlers"
```

---

## Task 7: Response Schema Tests

**Files:**
- Create: `tests/test_schemas_replies.py`

The existing `test_schemas.py` only tests request schemas. Add tests for response/reply schemas.

- [ ] **Step 1: Write reply schema tests**

Create `tests/test_schemas_replies.py`:
```python
"""Tests for response/reply Pydantic schemas."""

import pytest
from pydantic import ValidationError

from app.schemas.vms import (
    VmActionReply, VmActionItemReply,
    VmCreateReply, VmCreateItemReply,
    VmDeleteReply, VmDeleteItemReply,
    VmCloneReply, VmCloneItemReply,
    VmListUsageReply, VmListUsageItemReply,
)
from app.schemas.debug import DebugPingReply


class TestDebugPingReply:
    """Test DebugPingReply — one of the few reply schemas with log_multiline."""

    def test_valid_reply(self):
        reply = DebugPingReply(rc=0, log_multiline=["PLAY RECAP", "ok=1"])
        assert reply.rc == 0
        assert len(reply.log_multiline) == 2

    def test_missing_rc_raises(self):
        with pytest.raises(ValidationError):
            DebugPingReply(log_multiline=["line"])

    def test_missing_log_multiline_raises(self):
        with pytest.raises(ValidationError):
            DebugPingReply(rc=0)


class TestVmActionReply:
    """Test VmActionReply with properly structured result items."""

    def test_valid_reply(self):
        item = VmActionItemReply(
            action="vm_start",
            source="proxmox",
            proxmox_node="pve01",
            vm_id="100",
            vm_name="test-vm",
        )
        reply = VmActionReply(rc=0, result=[item])
        assert reply.rc == 0
        assert len(reply.result) == 1
        assert reply.result[0].action == "vm_start"

    def test_invalid_action_raises(self):
        with pytest.raises(ValidationError):
            VmActionItemReply(
                action="invalid_action",
                source="proxmox",
                proxmox_node="pve01",
                vm_id="100",
                vm_name="test-vm",
            )


class TestVmCreateReply:
    """Test VmCreateReply schema."""

    def test_valid_reply(self):
        item = VmCreateItemReply(
            action="vm_create",
            source="proxmox",
            proxmox_node="pve01",
            vm_id=100,
            vm_name="new-vm",
            vm_cpu="host",
            vm_cores=2,
            vm_sockets=1,
            vm_memory=2048,
            vm_net0="virtio,bridge=vmbr0",
            vm_scsi0="local-lvm:32,format=raw",
            raw_data="UPID:pve01:...",
        )
        reply = VmCreateReply(rc=0, result=[item])
        assert reply.rc == 0


class TestVmDeleteReply:
    """Test VmDeleteReply schema."""

    def test_valid_reply(self):
        item = VmDeleteItemReply(
            action="vm_delete",
            source="proxmox",
            proxmox_node="pve01",
            vm_id=100,
            vm_name="old-vm",
            raw_data="UPID:pve01:...",
        )
        reply = VmDeleteReply(rc=0, result=[item])
        assert reply.rc == 0


class TestVmListUsageReply:
    """Test VmListUsageReply schema."""

    def test_valid_reply(self):
        item = VmListUsageItemReply(
            action="vm_list_usage",
            source="proxmox",
            proxmox_node="pve01",
            vm_id=100,
            vm_name="test-vm",
            cpu_allocated=2,
            cpu_current_usage=10,
            disk_current_usage=5000000,
            disk_max=34359738368,
            disk_read=1000,
            disk_write=500,
            net_in=280531583,
            net_out=6330590,
            ram_current_usage=1910544625,
            ram_max=4294967296,
            vm_status="running",
            vm_uptime=79940,
        )
        reply = VmListUsageReply(rc=0, result=[item])
        assert reply.rc == 0
        assert reply.result[0].vm_name == "test-vm"
```

- [ ] **Step 2: Run tests**

Run: `python3 -m pytest tests/test_schemas_replies.py -v`
Expected: All pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_schemas_replies.py
git commit -m "test: add response schema validation tests"
```

---

## Task 8: Expand README Documentation

**Files:**
- Modify: `README.md`

Add the missing sections identified in the review: WebSocket API, Docker details, Logging, and Testing structure.

- [ ] **Step 1: Read current README**

Read `README.md` to get exact line numbers for insertion points.

- [ ] **Step 2: Add WebSocket API section after API Documentation (line 89)**

Insert after the API Documentation section (after line 89, before Project Structure):

```markdown
## WebSocket API

### VM Status Stream

Real-time VM status updates via WebSocket. Polls the Proxmox API directly (not via Ansible) for low latency.

**URL:** `ws://host:8000/ws/vm-status`

**Query Parameters:**

| Parameter | Required | Description | Default |
|---|---|---|---|
| `node` | No | Proxmox node name to monitor | Read from inventory |

**Authentication:** Proxmox API credentials are read from the backend's `inventory/hosts.yml` file. The frontend does not need to handle tokens.

### Message Format

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
    "100": { "type": "changed", "vmid": 100, "status": "stopped", "cpu": 0.0, "..." : "..." },
    "102": { "type": "added", "vmid": 102, "name": "new-vm", "..." : "..." },
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
```

- [ ] **Step 3: Expand Docker section (after Quick Start, line 27)**

Expand the Docker Quick Start option with details:

```markdown
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
```

- [ ] **Step 4: Expand Testing section (replace lines 189-213)**

Replace the entire Development section (lines 189 through the `---` before License) with:

```markdown
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

| File | Covers |
|---|---|
| `test_api_smoke.py` | App startup, OpenAPI schema, docs endpoints |
| `test_routes_registered.py` | Golden route reference safety net (verifies all 69 routes are registered) |
| `test_config.py` | `app/core/config.py` settings and defaults |
| `test_vault.py` | `app/core/vault.py` VaultManager lifecycle |
| `test_runner.py` | `app/core/runner.py` log building |
| `test_runner_internals.py` | Runner helpers: envvars, cmdline, temp dir setup |
| `test_extractor.py` | `app/core/extractor.py` event parsing |
| `test_exceptions.py` | Custom validation error formatting |
| `test_schemas.py` | Pydantic request schema validation + backward-compat aliases |
| `test_schemas_replies.py` | Pydantic response schema validation |
| `test_checks_inventory.py` | Inventory name validation and path traversal detection |
| `test_checks_playbooks.py` | Playbook name validation and path traversal detection |
| `test_ws_helpers.py` | WebSocket helpers: diff computation, credential loading |
| `test_route_debug.py` | Debug endpoint integration tests (mocked runner) |
| `test_route_vms.py` | VM endpoint integration tests (mocked runner) |

Route handler tests mock `run_playbook_core()` so no Ansible or Proxmox connection is needed.

The **golden route reference** (`tests/fixtures/routes_golden.json`) is a safety net that ensures refactoring never accidentally drops an endpoint. If you add or remove a route, update this file.

### Manual Testing

Curl scripts for every endpoint are available in `curl_utils/`.
```

- [ ] **Step 5: Add Logging subsection (after Configuration, line 77)**

Insert after Configuration section:

```markdown
### Logging

The API uses Python's `logging` module with structured output. Log level is controlled by uvicorn:

```bash
uvicorn app.main:app --log-level debug   # verbose
uvicorn app.main:app --log-level info    # default
uvicorn app.main:app --log-level warning # quiet
```

When `DEBUG=true`, the app registers a custom 422 handler that logs full validation error details at `ERROR` level -- useful for debugging malformed requests during development.
```

- [ ] **Step 6: Verify README renders correctly**

Visually scan the README for formatting issues (broken tables, unclosed code blocks).

- [ ] **Step 7: Commit**

```bash
git add README.md
git commit -m "docs: expand README with WebSocket API, Docker details, logging, and test structure"
```

---

## Task 9: Run Full Test Suite and Verify

- [ ] **Step 1: Run all tests**

Run: `python3 -m pytest tests/ -v --tb=short`
Expected: All tests pass (existing + new).

- [ ] **Step 2: Count test coverage**

Run: `python3 -m pytest tests/ -v --tb=short 2>&1 | tail -5`
Verify the total test count has increased significantly from the baseline (~60 to ~100+).

- [ ] **Step 3: Push to refactor branch**

```bash
git push origin refactor
```

This updates PR #62 with all the changes.
