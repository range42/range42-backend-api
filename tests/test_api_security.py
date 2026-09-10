"""Authentication must protect streams and mutations before route execution."""
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

TOKEN = "range42-test-bearer-token-32-characters"


def secured_app(monkeypatch, **overrides):
    from app import main
    from app.core.config import Settings
    monkeypatch.setenv("RANGE42_AUTH_MODE", "required")
    monkeypatch.setenv("RANGE42_API_TOKEN", TOKEN)
    settings = Settings()
    if overrides:
        settings = replace(settings, **overrides)
    monkeypatch.setattr(main, "settings", settings)
    app = main.create_app()
    app.get("/security-test")(lambda: {"ok": True})
    return app


@pytest.mark.parametrize("path", ["/security-test", "/v1/deployments/unknown/events", "/docs/openapi.json"])
def test_unauthenticated_requests_are_rejected_before_routing(monkeypatch, path):
    client = TestClient(secured_app(monkeypatch))
    response = client.get(path)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["code"] == "AUTH_REQUIRED"


@pytest.mark.parametrize("header", ["Bearer wrong", "Basic " + TOKEN, "Bearer " + TOKEN + "x"])
def test_incorrect_credentials_are_rejected(monkeypatch, header):
    response = TestClient(secured_app(monkeypatch)).get("/security-test", headers={"Authorization": header})
    assert response.status_code == 401
    assert TOKEN not in response.text


def test_correct_bearer_allows_http_and_does_not_accept_query_token(monkeypatch):
    client = TestClient(secured_app(monkeypatch))
    assert client.get("/security-test", headers={"Authorization": "Bearer " + TOKEN}).json() == {"ok": True}
    assert client.get("/security-test", params={"token": TOKEN}).status_code == 401
    assert client.get("/v1/health").status_code == 200


def test_required_mode_fails_closed_without_configured_token(monkeypatch):
    monkeypatch.delenv("RANGE42_API_TOKEN", raising=False)
    monkeypatch.delenv("RANGE42_API_TOKEN_FILE", raising=False)
    monkeypatch.setenv("RANGE42_AUTH_MODE", "required")
    from app.core.config import Settings
    from app import main
    monkeypatch.setattr(main, "settings", Settings())
    with pytest.raises(RuntimeError, match="RANGE42_API_TOKEN"):
        main.create_app()


def test_cors_supports_project_put_and_credentials_patch(monkeypatch):
    client = TestClient(secured_app(monkeypatch))
    for method in ("PUT", "PATCH"):
        response = client.options("/v1/projects/p", headers={
            "Origin": "http://localhost:3002", "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": "authorization,content-type,last-event-id",
        })
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:3002"
    response = client.options("/v1/projects/p", headers={
        "Origin": "http://192.168.42.99:3002", "Access-Control-Request-Method": "PUT",
    })
    assert "access-control-allow-origin" not in response.headers


def test_exact_cors_origins_support_deployed_ui(monkeypatch):
    monkeypatch.setenv("RANGE42_CORS_ORIGINS", "https://range42.example")
    client = TestClient(secured_app(monkeypatch))
    response = client.options("/security-test", headers={
        "Origin": "https://range42.example", "Access-Control-Request-Method": "GET",
    })
    assert response.headers["access-control-allow-origin"] == "https://range42.example"
