
from fastapi import APIRouter

# /v0/admin/proxmox/*
from app.routes.admin_proxmox          import router as admin_proxmox_routers

# /v0/admin/debug/*
from app.routes.admin_debug            import router as admin_debug_routers

# /v0/admin/run/*
from app.routes.admin_run              import router as admin_run_routers

# /v0/admin/run/actions/core/*
from app.routes.admin_run_bundles_core import router as admin_run_bundles_core_routers

#######################################################################################################################

router = APIRouter()

# /v0/admin/debug/*
router.include_router(admin_debug_routers)

# /v0/admin/run/actions/core/*
router.include_router(admin_run_bundles_core_routers)

# /v0/run/actions|scenarios/run*
router.include_router(admin_run_routers)

# /v0/admin/proxmox/*
router.include_router(admin_proxmox_routers)

####
