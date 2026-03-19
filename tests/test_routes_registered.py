"""Verify all routes from the golden reference are registered after consolidation."""

import json
from pathlib import Path


def test_all_routes_preserved(client):
    golden_path = Path(__file__).parent / "fixtures" / "routes_golden.json"
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
