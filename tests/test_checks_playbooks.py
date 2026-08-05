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

    def test_accepts_dotted_segment_format(self):
        """Dotted <subject>.<verb>.<object> names pass format validation
        (range42-playbooks#133) and fail only on the missing file.

        Asserting FileNotFoundError specifically is what makes this test
        meaningful: the old regex rejected dots with HTTPException(400), so
        a `raises((HTTPException, FileNotFoundError))` would pass either way.
        """
        with pytest.raises(FileNotFoundError):
            resolve_bundles_playbook(
                "generic/systems.baseline.docker_host", "www_app")

    def test_rejects_dot_segment(self):
        with pytest.raises(HTTPException) as exc_info:
            resolve_actions_playbook("foo/../bar", "www_app")
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
