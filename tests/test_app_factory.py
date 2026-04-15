def test_create_app_returns_fastapi_instance():
    from app.main import create_app

    app = create_app()
    assert app.title == "CR42 - API"
    assert app.version == "v0.1"


def test_app_factory_warns_on_multi_worker(monkeypatch, caplog):
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    monkeypatch.setenv("RANGE42_UVICORN_WORKERS_GUARD", "1")
    from importlib import reload
    from app.core import config as cfg
    reload(cfg)
    from app import main as m
    m.create_app()
