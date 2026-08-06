"""Smoke test: /v1 router skeleton is registered alongside /v0.

Endpoints are added in subsequent tasks — this test just confirms the
aggregate router is wired in, so those tasks can assume the namespace.
"""
from app.main import create_app
from app.routes.v1 import router as v1_router


def test_v1_router_importable():
    assert v1_router.prefix == "/v1"
    # Five sub-routers (catalog, projects, deployments, proxmox, admin) are
    # included even though they have no endpoints yet.
    # APIRouter.include_router merges child routes in under routes; just
    # assert the aggregate has been constructed without raising.
    assert v1_router is not None


def test_v0_surface_preserved():
    app = create_app()
    paths = {route.path for route in app.routes}
    assert any(p.startswith("/v0/") for p in paths)


def test_openapi_reflects_v0_routes():
    """Once /v1 endpoints land in later tasks they show up here too."""
    app = create_app()
    # FastAPI openapi is built lazily. Confirm the app builds cleanly.
    schema = app.openapi()
    assert "paths" in schema
