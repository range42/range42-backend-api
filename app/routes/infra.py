"""Infrastructure health routes.

Endpoints
---------
- ``GET /v1/infra/mirror/health`` -- APT mirror health status.
"""

import os

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()

_MIRROR_REPORT_PATH = "/acng-report.html"


def _mirror_base_url() -> str:
    host = os.getenv("APT_MIRROR_HOST", "")
    port = os.getenv("APT_MIRROR_NGINX_PORT", "80")
    return f"http://{host}:{port}"


@router.get(
    path="/mirror/health",
    summary="APT mirror health",
    description="Returns health status and basic stats for the local APT mirror.",
    tags=["infra"],
)
def infra_mirror_health() -> JSONResponse:
    """Proxy the apt-cacher-ng report page and return structured health info.

    :returns: JSON with status, backend, and report URL.
    :rtype: JSONResponse
    """
    if not os.getenv("APT_MIRROR_HOST"):
        return JSONResponse(
            {"status": "unconfigured", "detail": "APT_MIRROR_HOST not set"},
            status_code=503,
        )

    base = _mirror_base_url()

    try:
        resp = httpx.get(f"{base}{_MIRROR_REPORT_PATH}", timeout=5.0)
        if resp.status_code == 200:
            return JSONResponse(
                {
                    "status": "healthy",
                    "backend": "aptly" if os.getenv("APT_MIRROR_AIRGAPPED") else "acng",
                    "report_url": f"{base}{_MIRROR_REPORT_PATH}",
                }
            )
        return JSONResponse(
            {"status": "degraded", "http_status": resp.status_code},
            status_code=502,
        )
    except httpx.ConnectError:
        return JSONResponse({"status": "offline", "detail": "connection refused"}, status_code=503)
    except httpx.TimeoutException:
        return JSONResponse({"status": "offline", "detail": "timeout"}, status_code=503)
