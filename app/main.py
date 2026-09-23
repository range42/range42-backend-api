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
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.auth import BearerAuthMiddleware, configured_api_token
from app.core.access import configured_principals
from app.core.errors import install_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware_trace import TraceIdMiddleware
from app.core.maintenance import MaintenanceGate, MaintenanceMiddleware
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
    from app.core.credential_store import encrypt_legacy_credentials
    await encrypt_legacy_credentials(get_engine())
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
        await app.state.maintenance_gate.drain()
        if tmp_dir and tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
        from app.core.orphans import stop_observers
        await stop_observers()
        await dispose_engine()


def create_app() -> FastAPI:
    """Application factory. Creates and configures the FastAPI application."""
    configure_logging(json_output=True)

    token = configured_api_token(settings)
    principals = configured_principals(settings)
    if settings.auth_mode == "required" or principals:
        from app.core.credential_store import credential_cipher
        credential_cipher(settings)
    maintenance_gate = MaintenanceGate(
        Path(settings.maintenance_lock_file) if settings.maintenance_lock_file else None
    )
    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origin_regex=settings.cors_origin_regex,
            allow_origins=list(settings.cors_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "Accept", "Authorization", "Last-Event-ID", "X-Range42-Trace-Id", "X-Range42-Reservation-Token"],
            expose_headers=["X-Range42-Trace-Id", "X-Range42-Audit-Id", "X-Range42-Audit-State"],
            max_age=600,
        ),
        Middleware(TraceIdMiddleware),
        Middleware(BearerAuthMiddleware, token=token, principals=principals, audit_enabled=settings.audit_enabled),
        Middleware(MaintenanceMiddleware, gate=maintenance_gate),
    ]

    _app = FastAPI(
        title="CR42 - API",
        lifespan=lifespan,
        docs_url="/docs/swagger",
        redoc_url="/docs/redoc",
        openapi_url="/docs/openapi.json",
        version="v0.1",
        license_info={"name": "GPLv3"},
        contact={"email": "info@nc3.lu"},
        middleware=middleware,
    )

    install_exception_handlers(_app)
    _app.state.maintenance_gate = maintenance_gate
    _app.include_router(api_router)
    _app.include_router(ws_router)

    from app.routes.v1 import router as v1_router
    _app.include_router(v1_router)

    workers_env = os.getenv("WEB_CONCURRENCY") or os.getenv("UVICORN_WORKERS")
    if settings.uvicorn_workers_guard and workers_env:
        try:
            if int(workers_env) > 1:
                get_logger(__name__).warning(
                    "multi_worker_deploy_invariant_violated",
                    workers=workers_env,
                    remediation=(
                        "Set WEB_CONCURRENCY=1 in deploy environments; SSE "
                        "open_streams counter and live subscriber map are "
                        "in-process. Set RANGE42_UVICORN_WORKERS_GUARD=0 to silence."
                    ),
                )
        except ValueError:
            pass

    return _app


app = create_app()
