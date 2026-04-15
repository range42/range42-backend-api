from fastapi import APIRouter

from app.routes.v1.proxmox.hosts import router as hosts_router

router = APIRouter(prefix="/proxmox", tags=["v1 proxmox"])
router.include_router(hosts_router)
