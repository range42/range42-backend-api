"""GET /v1/infra/mirror/health — the endpoint InfraNodeMirror.vue polls."""
import httpx
import pytest
from fastapi.testclient import TestClient

REPORT = "/acng-report.html"


@pytest.fixture
def client(monkeypatch):
    from app.main import app
    return TestClient(app)


def test_unconfigured_when_host_unset(client, monkeypatch):
    monkeypatch.delenv("APT_MIRROR_HOST", raising=False)
    r = client.get("/v1/infra/mirror/health")
    assert r.status_code == 503
    assert r.json()["status"] == "unconfigured"


def test_healthy_reports_backend_and_report_url(client, monkeypatch):
    monkeypatch.setenv("APT_MIRROR_HOST", "10.0.0.9")
    monkeypatch.setenv("APT_MIRROR_NGINX_PORT", "8080")
    monkeypatch.delenv("APT_MIRROR_AIRGAPPED", raising=False)
    monkeypatch.setattr(
        "app.routes.infra.httpx.get",
        lambda url, timeout=None: httpx.Response(200, request=httpx.Request("GET", url)),
    )
    body = client.get("/v1/infra/mirror/health").json()
    assert body["status"] == "healthy"
    assert body["backend"] == "acng"
    assert body["report_url"] == f"http://10.0.0.9:8080{REPORT}"


def test_airgapped_flag_switches_backend_label(client, monkeypatch):
    monkeypatch.setenv("APT_MIRROR_HOST", "10.0.0.9")
    monkeypatch.setenv("APT_MIRROR_AIRGAPPED", "true")
    monkeypatch.setattr(
        "app.routes.infra.httpx.get",
        lambda url, timeout=None: httpx.Response(200, request=httpx.Request("GET", url)),
    )
    assert client.get("/v1/infra/mirror/health").json()["backend"] == "aptly"


def test_non_200_is_degraded(client, monkeypatch):
    monkeypatch.setenv("APT_MIRROR_HOST", "10.0.0.9")
    monkeypatch.setattr(
        "app.routes.infra.httpx.get",
        lambda url, timeout=None: httpx.Response(500, request=httpx.Request("GET", url)),
    )
    r = client.get("/v1/infra/mirror/health")
    assert r.status_code == 502
    assert r.json() == {"status": "degraded", "http_status": 500}


@pytest.mark.parametrize("exc,detail", [
    (httpx.ConnectError("refused"), "connection refused"),
    (httpx.TimeoutException("timeout"), "timeout"),
    (httpx.ConnectTimeout("connect timed out"), "timeout"),
    # Mirror accepted the connection then broke it — these are the ones the
    # first version missed, and they surfaced as 500s.
    (httpx.ReadError("reset"), "ReadError"),
    (httpx.RemoteProtocolError("truncated"), "RemoteProtocolError"),
    (httpx.WriteError("broken pipe"), "WriteError"),
    (httpx.ProtocolError("bad framing"), "ProtocolError"),
])
def test_transport_failures_are_offline(client, monkeypatch, exc, detail):
    """The node polls every 30s — a down mirror must not surface as a 500."""
    monkeypatch.setenv("APT_MIRROR_HOST", "10.0.0.9")

    def _boom(url, timeout=None):
        raise exc

    monkeypatch.setattr("app.routes.infra.httpx.get", _boom)
    r = client.get("/v1/infra/mirror/health")
    assert r.status_code == 503
    assert r.json() == {"status": "offline", "detail": detail}


@pytest.mark.parametrize("value,expected", [
    ("true", "aptly"), ("True", "aptly"), ("1", "aptly"), ("yes", "aptly"),
    # Explicitly disabling it must mean acng — presence alone is not truth.
    ("false", "acng"), ("0", "acng"), ("no", "acng"), ("", "acng"),
])
def test_airgapped_flag_is_parsed_as_boolean(client, monkeypatch, value, expected):
    monkeypatch.setenv("APT_MIRROR_HOST", "10.0.0.9")
    monkeypatch.setenv("APT_MIRROR_AIRGAPPED", value)
    monkeypatch.setattr(
        "app.routes.infra.httpx.get",
        lambda url, timeout=None: httpx.Response(200, request=httpx.Request("GET", url)),
    )
    assert client.get("/v1/infra/mirror/health").json()["backend"] == expected
