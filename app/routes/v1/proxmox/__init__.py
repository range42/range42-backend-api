from fastapi import APIRouter

from app.routes.v1.proxmox.allocations import router as allocations_router
from app.routes.v1.proxmox.capacity import router as capacity_router
from app.routes.v1.proxmox.hosts import router as hosts_router
from app.routes.v1.proxmox.sdn import router as sdn_router
from app.routes.v1.proxmox.runtime_capabilities import router as runtime_capabilities_router
from app.routes.v1.proxmox.snapshots import router as snapshots_router
from app.routes.v1.proxmox.storage import router as storage_router
from app.routes.v1.proxmox.vms import router as vms_router
from app.routes.v1.proxmox.vm_config import router as vm_config_router
from app.routes.v1.proxmox.vm_hardware import router as vm_hardware_router

router = APIRouter(prefix="/proxmox", tags=["v1 proxmox"])
router.include_router(hosts_router)
router.include_router(vms_router)
router.include_router(vm_config_router)
router.include_router(vm_hardware_router)
router.include_router(storage_router)
router.include_router(snapshots_router)
router.include_router(allocations_router)
router.include_router(capacity_router)

router.include_router(sdn_router)
router.include_router(runtime_capabilities_router)
