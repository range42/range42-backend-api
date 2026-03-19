import os
from pathlib import Path


def test_settings_loads_from_env(monkeypatch):
    monkeypatch.setenv("PROJECT_ROOT_DIR", "/tmp/test-project")
    monkeypatch.setenv("CORS_ORIGIN_REGEX", r"^https?://example\.com$")

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

    assert "localhost" in config_mod.Settings().cors_origin_regex


def test_settings_playbook_path(monkeypatch):
    monkeypatch.setenv("PROJECT_ROOT_DIR", "/tmp/test-project")

    import importlib
    import app.core.config as config_mod
    importlib.reload(config_mod)

    s = config_mod.Settings()
    assert s.playbook_path == Path("/tmp/test-project/playbooks/generic.yml")
    assert s.inventory_name == "hosts"
