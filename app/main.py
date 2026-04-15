"""FastAPI application factory for the Range42 Backend API.

Creates and configures the FastAPI app with CORS middleware, vault
lifecycle management, custom exception handlers, and route registration.
The module-level ``app`` object is the ASGI entry point used by uvicorn.
"""

import os
import shutil
import stat
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.exceptions import validation_exception_handler
from app.core.logging import configure_logging, get_logger
from app.core.middleware_trace import TraceIdMiddleware
from app.core.runner import vault_manager
from app.routes import router as api_router
from app.routes.ws_status import router as ws_router

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage vault password lifecycle: setup on startup, cleanup on shutdown."""
    tmp_dir: Path | None = None

    if settings.vault_password_file:
        p = Path(settings.vault_password_file)
        if not p.exists():
            raise RuntimeError(f"Vault password file not found: {p}")
        vault_manager.set_vault_path(p)
        logger.info("Using VAULT_PASSWORD_FILE=%s", p)

    elif settings.vault_password:
        tmp_dir = Path(tempfile.mkdtemp(prefix="vault-"))
        f = tmp_dir / "vault_pass.txt"
        f.write_text(settings.vault_password)
        os.chmod(f, stat.S_IRUSR | stat.S_IWUSR)
        vault_manager.set_vault_path(f)
        logger.info("Using VAULT_PASSWORD (temp file)")

    else:
        logger.warning("No vault password provided")

    # v1 state layer
    from app.core.db import get_engine, dispose_engine
    engine = get_engine()
    logger.info("v1 state engine ready", db_url=settings.db_url)

    # v1 orphan reconcile: run once synchronously at boot so the structured
    # log captures any detached ansible-runner subprocesses inherited from
    # a prior FastAPI process, then register the periodic apscheduler job.
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from app.core.orphans import reconcile_once
    scheduler = AsyncIOScheduler()
    scheduler.add_job(reconcile_once, "interval",
                      seconds=settings.orphan_reconcile_interval_s,
                      id="orphan_reconcile", max_instances=1, coalesce=True)
    try:
        await reconcile_once()
    except Exception as exc:  # noqa: BLE001
        logger.warning("orphan reconcile boot failed", error=str(exc))
    scheduler.start()

    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        if tmp_dir and tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
        await dispose_engine()


def create_app() -> FastAPI:
    """Application factory. Creates and configures the FastAPI application."""
    configure_logging(json_output=True)

    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origin_regex=settings.cors_origin_regex,
            allow_credentials=True,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "Accept", "Authorization"],
            max_age=600,
        ),
        Middleware(TraceIdMiddleware),
    ]

    _app = FastAPI(
        title="CR42 - API",
        lifespan=lifespan,
        docs_url="/docs/swagger",
        redoc_url="/docs/redoc",
        openapi_url="/docs/openapi.json",
        version="v0.1",
        license_info={"name": "GPLv3"},
        contact={"email": "info@digisquad.com"},
        middleware=middleware,
    )

    _app.add_exception_handler(RequestValidationError, validation_exception_handler)
    _app.include_router(api_router)
    _app.include_router(ws_router)

    return _app


app = create_app()
