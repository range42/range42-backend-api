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


def test_settings_v1_defaults(monkeypatch):
    for k in ("RANGE42_WORKSPACE_ROOT", "RANGE42_DB_URL",
              "RANGE42_REDACTION_DENYLIST", "RANGE42_ORPHAN_RECONCILE_INTERVAL",
              "RANGE42_RUNNER_BIN", "RANGE42_UVICORN_WORKERS_GUARD"):
        monkeypatch.delenv(k, raising=False)
    from importlib import reload
    from app.core import config as cfg_mod
    reload(cfg_mod)
    s = cfg_mod.Settings()
    assert s.workspace_root == Path.home() / "range42.config"
    assert s.db_url == f"sqlite+aiosqlite:///{Path.home()}/range42.config/.range42.db"
    assert ".git" not in s.db_url
    assert "*_password" in s.redaction_denylist
    assert "*_token" in s.redaction_denylist
    assert s.orphan_reconcile_interval_s == 300
    assert s.runner_bin == "ansible-runner"
    assert s.uvicorn_workers_guard is True


def test_settings_v1_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("RANGE42_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("RANGE42_DB_URL", "sqlite+aiosqlite:///" + str(tmp_path / "r.db"))
    monkeypatch.setenv("RANGE42_REDACTION_DENYLIST", "*_secret,*_key")
    monkeypatch.setenv("RANGE42_ORPHAN_RECONCILE_INTERVAL", "60")
    from importlib import reload
    from app.core import config as cfg_mod
    reload(cfg_mod)
    s = cfg_mod.Settings()
    assert s.workspace_root == tmp_path
    assert s.orphan_reconcile_interval_s == 60
    assert s.redaction_denylist == ("*_secret", "*_key")
