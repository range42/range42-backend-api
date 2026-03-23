import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_PROJECT_ROOT = str(Path(__file__).resolve().parents[1])

# Set ALL env vars that are read at module-import time.
# Routes call Path(os.getenv("PROJECT_ROOT_DIR")).resolve() at import time.
os.environ.setdefault("PROJECT_ROOT_DIR", _PROJECT_ROOT)
os.environ.setdefault("API_BACKEND_WWWAPP_PLAYBOOKS_DIR", _PROJECT_ROOT)
os.environ.setdefault("API_BACKEND_PUBLIC_PLAYBOOKS_DIR", _PROJECT_ROOT)
os.environ.setdefault(
    "API_BACKEND_INVENTORY_DIR", str(Path(_PROJECT_ROOT) / "inventory")
)


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


@pytest.fixture(scope="session")
def openapi_schema():
    from app.main import app

    c = TestClient(app)
    resp = c.get("/docs/openapi.json")
    return resp.json()
