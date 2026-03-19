import json
from pathlib import Path


def test_app_starts(client):
    assert client is not None


def test_openapi_schema_loads(client):
    resp = client.get("/docs/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert schema["info"]["title"] == "CR42 - API"
    assert schema["info"]["version"] == "v0.1"


def test_swagger_docs_available(client):
    resp = client.get("/docs/swagger")
    assert resp.status_code == 200


def test_redoc_docs_available(client):
    resp = client.get("/docs/redoc")
    assert resp.status_code == 200


def test_all_routes_match_golden_reference(client):
    golden_path = Path(__file__).parent / "fixtures" / "routes_golden.json"
    assert golden_path.exists(), f"Golden reference not found at {golden_path}"

    with open(golden_path) as f:
        golden_routes = json.load(f)

    resp = client.get("/docs/openapi.json")
    schema = resp.json()
    registered = {}
    for path, methods in schema.get("paths", {}).items():
        for method in methods:
            if method.upper() in ("GET", "POST", "PUT", "DELETE", "PATCH"):
                registered.setdefault(path, []).append(method.upper())

    missing = []
    for path, methods in golden_routes.items():
        for method in methods:
            if method not in registered.get(path, []):
                missing.append(f"{method} {path}")

    assert not missing, f"Missing {len(missing)} routes:\n" + "\n".join(sorted(missing))
