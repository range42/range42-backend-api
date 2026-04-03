"""Tests for app.utils.vm_id_name_resolver."""

import pytest
from unittest.mock import patch
from fastapi import HTTPException

from app.utils.vm_id_name_resolver import hack_same_vm_id, resolv_id_to_vm_name


class TestHackSameVmId:
    """Tests for hack_same_vm_id() -- flexible VM ID comparison."""

    def test_int_int_equal(self):
        assert hack_same_vm_id(100, 100) is True

    def test_int_int_not_equal(self):
        assert hack_same_vm_id(100, 200) is False

    def test_str_str_equal(self):
        assert hack_same_vm_id("100", "100") is True

    def test_str_str_not_equal(self):
        assert hack_same_vm_id("100", "200") is False

    def test_int_str_equal(self):
        assert hack_same_vm_id(100, "100") is True

    def test_str_int_equal(self):
        assert hack_same_vm_id("100", 100) is True

    def test_int_str_not_equal(self):
        assert hack_same_vm_id(100, "200") is False

    def test_none_falls_back_to_string(self):
        assert hack_same_vm_id(None, None) is True

    def test_none_vs_string(self):
        assert hack_same_vm_id(None, "100") is False

    def test_non_numeric_strings(self):
        assert hack_same_vm_id("abc", "abc") is True
        assert hack_same_vm_id("abc", "def") is False


class TestResolvIdToVmName:
    """Tests for resolv_id_to_vm_name() with mocked runner."""

    @pytest.fixture
    def mock_runner_with_results(self):
        """Mock run_playbook_core returning VM list data."""
        events = [{"event": "runner_on_ok"}]
        result_data = [
            [
                {"vm_id": 100, "vm_name": "pmg01"},
                {"vm_id": 101, "vm_name": "zbx01"},
                {"vm_id": 200, "vm_name": "test-vm"},
            ]
        ]
        with patch("app.utils.vm_id_name_resolver.run_playbook_core") as mock_run, \
             patch("app.utils.vm_id_name_resolver.extract_action_results") as mock_extract:
            mock_run.return_value = (0, events, "ok", "ok")
            mock_extract.return_value = result_data
            yield mock_run, mock_extract

    def test_finds_vm_by_int_id(self, mock_runner_with_results):
        result = resolv_id_to_vm_name("pve01", "100")
        assert result["vm_id"] == 100
        assert result["vm_name"] == "pmg01"

    def test_finds_vm_by_string_id(self, mock_runner_with_results):
        result = resolv_id_to_vm_name("pve01", "200")
        assert result["vm_name"] == "test-vm"

    def test_not_found_raises_500(self, mock_runner_with_results):
        with pytest.raises(HTTPException) as exc_info:
            resolv_id_to_vm_name("pve01", "9999")
        assert exc_info.value.status_code == 500
        assert "NOT FOUND" in exc_info.value.detail

    def test_invalid_json_string_raises_500(self):
        with patch("app.utils.vm_id_name_resolver.run_playbook_core") as mock_run, \
             patch("app.utils.vm_id_name_resolver.extract_action_results") as mock_extract:
            mock_run.return_value = (0, [], "ok", "ok")
            mock_extract.return_value = "not valid json {"
            with pytest.raises(HTTPException) as exc_info:
                resolv_id_to_vm_name("pve01", "100")
            assert exc_info.value.status_code == 500

    def test_empty_results_raises_500(self):
        with patch("app.utils.vm_id_name_resolver.run_playbook_core") as mock_run, \
             patch("app.utils.vm_id_name_resolver.extract_action_results") as mock_extract:
            mock_run.return_value = (0, [], "ok", "ok")
            mock_extract.return_value = []
            with pytest.raises(HTTPException) as exc_info:
                resolv_id_to_vm_name("pve01", "100")
            assert exc_info.value.status_code == 500
