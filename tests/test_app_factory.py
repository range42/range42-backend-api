def test_create_app_returns_fastapi_instance():
    from app.main import create_app
    app = create_app()
    assert app.title == "CR42 - API"
    assert app.version == "v0.1"
