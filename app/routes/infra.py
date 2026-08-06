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


def _airgapped() -> bool:
    """Whether the mirror is aptly (air-gapped) rather than apt-cacher-ng.

    Parsed as a boolean, not mere presence: a deployment that sets
    ``APT_MIRROR_AIRGAPPED=false`` means acng, but any non-empty string is
    truthy. Matches the ``("1", "true", "yes")`` convention in core.config.
    """
    return os.getenv("APT_MIRROR_AIRGAPPED", "").lower() in ("1", "true", "yes")


def _offline(detail: str) -> JSONResponse:
    return JSONResponse({"status": "offline", "detail": detail}, status_code=503)


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
                    "backend": "aptly" if _airgapped() else "acng",
                    "report_url": f"{base}{_MIRROR_REPORT_PATH}",
                }
            )
        return JSONResponse(
            {"status": "degraded", "http_status": resp.status_code},
            status_code=502,
        )
    except httpx.TimeoutException:
        return _offline("timeout")
    except httpx.ConnectError:
        return _offline("connection refused")
    except httpx.RequestError as exc:
        # Everything else httpx can raise while talking to the mirror —
        # ReadError, RemoteProtocolError, a reset mid-response. The canvas
        # node polls this every 30s, so a broken mirror must read as offline
        # rather than a 500 that makes the backend look at fault.
        logger.warning("mirror health transport error",
                       error=type(exc).__name__, detail=str(exc))
        return _offline(type(exc).__name__)
