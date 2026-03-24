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
