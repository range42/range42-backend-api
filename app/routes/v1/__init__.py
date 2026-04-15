"""Aggregate /v1 APIRouter — wires catalog/projects/deployments/proxmox/admin sub-families."""
from fastapi import APIRouter

from app.routes.v1.catalog import router as catalog_router
from app.routes.v1.projects import router as projects_router
from app.routes.v1.deployments import router as deployments_router
from app.routes.v1.proxmox import router as proxmox_router
from app.routes.v1.admin import router as admin_router
from app.routes.v1.health import router as health_router

router = APIRouter(prefix="/v1")
router.include_router(catalog_router)
router.include_router(projects_router)
router.include_router(deployments_router)
router.include_router(proxmox_router)
router.include_router(admin_router)
router.include_router(health_router)
