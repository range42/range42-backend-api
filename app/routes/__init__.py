from fastapi import APIRouter

from app.routes.debug import router as debug_router
from app.routes.firewall import router as firewall_router
from app.routes.network import router as network_router
from app.routes.runner import run_bundle, run_scenario
from app.routes.snapshots import router as snapshots_router
from app.routes.storage import storage_name_router, storage_router
from app.routes.vm_config import router as vm_config_router
from app.routes.vms import vm_id_router, vms_router

router = APIRouter()

# /v0/admin/debug/*
router.include_router(debug_router, prefix="/v0/admin/debug")

# /v0/admin/run/bundles/{name}/run
# The former hardcoded /core/* bundle routes are gone: their bundles are
# retired to decom/ in range42-playbooks#137 and the generic/ tier replaced
# them with composed BASELINE_* profiles. Use the generic runner below.
_bundles_runner = APIRouter()
_bundles_runner.add_api_route(
    "/{bundles_name}/run",
    run_bundle,
    methods=["POST"],
    summary="Run bundles",
    description="Run generic bundles with default (and static) extras_vars ",
    tags=["runner"],
)
router.include_router(_bundles_runner, prefix="/v0/admin/run/bundles")

# /v0/admin/run/scenarios/{name}/run
_scenarios_runner = APIRouter()
_scenarios_runner.add_api_route(
    "/{scenario_name}/run",
    run_scenario,
    methods=["POST"],
    summary="Run scenario",
    description="Run generic scenario with default (and static) extras_vars ",
    tags=["runner"],
)
router.include_router(_scenarios_runner, prefix="/v0/admin/run/scenarios")

# /v0/admin/proxmox/vms
router.include_router(vms_router, prefix="/v0/admin/proxmox/vms")

# /v0/admin/proxmox/vms/vm_id
router.include_router(vm_id_router, prefix="/v0/admin/proxmox/vms/vm_id")

# /v0/admin/proxmox/vms/vm_id/config
router.include_router(vm_config_router, prefix="/v0/admin/proxmox/vms/vm_id/config")

# /v0/admin/proxmox/vms/vm_id/snapshot
router.include_router(snapshots_router, prefix="/v0/admin/proxmox/vms/vm_id/snapshot")

# /v0/admin/proxmox/storage/storage_name
router.include_router(
    storage_name_router, prefix="/v0/admin/proxmox/storage/storage_name"
)

# /v0/admin/proxmox/storage
router.include_router(storage_router, prefix="/v0/admin/proxmox/storage")

# /v0/admin/proxmox/firewall
router.include_router(firewall_router, prefix="/v0/admin/proxmox/firewall")

# /v0/admin/proxmox/network
router.include_router(network_router, prefix="/v0/admin/proxmox/network")
