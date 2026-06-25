from fastapi import APIRouter

from app.routes.v1.proxmox.hosts import router as hosts_router
from app.routes.v1.proxmox.storage import router as storage_router
from app.routes.v1.proxmox.vms import router as vms_router

router = APIRouter(prefix="/proxmox", tags=["v1 proxmox"])
router.include_router(hosts_router)
router.include_router(vms_router)
router.include_router(storage_router)
