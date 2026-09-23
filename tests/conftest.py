import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_PROJECT_ROOT = str(Path(__file__).resolve().parents[1])

# Tests explicitly opt into local development; production remains closed.
os.environ.setdefault("RANGE42_AUTH_MODE", "development")
# Fixed, public test key: never used by production.
os.environ.setdefault("RANGE42_CREDENTIAL_KEY", "dGVzdC1vbmx5LW5vdC1hLXByb2R1Y3Rpb24ta2V5ISE=")

# Synthetic forge names used by fixtures are explicitly approved in tests.
os.environ.setdefault("RANGE42_GIT_ALLOWED_HOSTS", "github.com,gitlab.com,codeberg.org,g.com,x,gitlab.example")

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


@pytest.fixture(autouse=True)
async def finish_deployment_observers():
    """Keep each test's background lifecycle work on its own event loop."""
    yield
    import asyncio
    from app.core.deploy_trigger import _BACKGROUND_TASKS

    tasks = {task for task in _BACKGROUND_TASKS
             if task.get_loop() is asyncio.get_running_loop()}
    if tasks:
        _, pending = await asyncio.wait(tasks, timeout=2)
        for task in pending:
            task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        _BACKGROUND_TASKS.difference_update(tasks)
        assert not [result for result in results if isinstance(result, Exception)]
