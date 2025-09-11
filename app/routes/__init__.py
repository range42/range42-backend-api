
from fastapi import APIRouter

# from app.routes.utils.ping import router as ping_router
# from app.routes.proxmox.list_vm import router as list_vm_router
# from app.routes.proxmox.snapshots.lxc import router as snaps_lxc_router
# from app.routes.proxmox.snapshots.vm import router as snaps_vm_router
# from app.routes.run.actions.catalog_run import catalog_run_router


# runner routes - generic
from app.routes.v0.run.actions.actions_run import router as actions_run_router
from app.routes.v0.run.scenarios.scenarios_run import router as scenarios_run_router

# vm list and usage
from app.routes.v0.proxmox.vms.list import router        as proxmox_vms_list_router
from app.routes.v0.proxmox.vms.list_usage import router  as proxmox_vms_list_usage_router

#
# proxmox vm life cycle mgmt
#

from app.routes.v0.proxmox.vms.vm_id.start import router      as proxmox_vms_vm_id_start
from app.routes.v0.proxmox.vms.vm_id.stop import router       as proxmox_vms_vm_id_stop_router
from app.routes.v0.proxmox.vms.vm_id.pause import router      as proxmox_vms_vm_id_pause_router
from app.routes.v0.proxmox.vms.vm_id.resume import router     as proxmox_vms_vm_id_resume_router
from app.routes.v0.proxmox.vms.vm_id.stop_force import router as proxmox_vms_vm_id_stop_force_router

#vm create delete clone
from app.routes.v0.proxmox.vms.vm_id.create import router              as proxmox_vms_vm_id_create_router
from app.routes.v0.proxmox.vms.vm_id.delete import router              as proxmox_vms_vm_id_delete_router
from app.routes.v0.proxmox.vms.vm_id.clone import router               as proxmox_vms_vm_id_clone_router

#config
from app.routes.v0.proxmox.vms.vm_id.config.vm_get_config import router       as proxmox_vms_vm_id_vm_get_config_router
from app.routes.v0.proxmox.vms.vm_id.config.vm_get_config_cdrom import router as proxmox_vms_vm_id_vm_get_config_cdrom_router
from app.routes.v0.proxmox.vms.vm_id.config.vm_get_config_cpu import router   as proxmox_vms_vm_id_vm_get_config_cpu_router
from app.routes.v0.proxmox.vms.vm_id.config.vm_get_config_ram import router   as proxmox_vms_vm_id_vm_get_config_ram_router
from app.routes.v0.proxmox.vms.vm_id.config.vm_set_tag import router          as proxmox_vms_vm_id_vm_set_tags_router


#snapshot
from app.routes.v0.proxmox.vms.vm_id.snapshots.vm_create import router as proxmox_vms_vm_id_vm_snapshot_create_router
from app.routes.v0.proxmox.vms.vm_id.snapshots.vm_list   import router as proxmox_vms_vm_id_vm_snapshot_list_router
from app.routes.v0.proxmox.vms.vm_id.snapshots.vm_delete import router as proxmox_vms_vm_id_vm_snapshot_delete_router
from app.routes.v0.proxmox.vms.vm_id.snapshots.vm_revert import router as proxmox_vms_vm_id_vm_snapshot_revert_router

# storage
from app.routes.v0.proxmox.storage.storage_name.list_iso      import router as proxmox_storage_with_storage_name_list_iso_router
from app.routes.v0.proxmox.storage.storage_name.list_template import router as proxmox_storage_with_storage_name_list_template_router

from app.routes.v0.proxmox.storage.list                       import router as proxmox_storage_list_router
from app.routes.v0.proxmox.storage.download_iso               import router as proxmox_storage_download_iso

#
# debug routes
#

from app.routes.v0.debug.ping import router as debug_ping

#######################################################################################################################

router = APIRouter()

# /v0/debug/
router.include_router(debug_ping, prefix="/v0/debug")

# /v0/proxmox/vms
router.include_router(proxmox_vms_list_router, prefix="/v0/proxmox/vms")
router.include_router(proxmox_vms_list_usage_router,                prefix="/v0/proxmox/vms")

# /v0/proxmox/vms/vm_id/
router.include_router(proxmox_vms_vm_id_start,                      prefix="/v0/proxmox/vms/vm_id")
router.include_router(proxmox_vms_vm_id_stop_router,                prefix="/v0/proxmox/vms/vm_id")
router.include_router(proxmox_vms_vm_id_stop_force_router,          prefix="/v0/proxmox/vms/vm_id")
router.include_router(proxmox_vms_vm_id_pause_router,               prefix="/v0/proxmox/vms/vm_id")
router.include_router(proxmox_vms_vm_id_resume_router,              prefix="/v0/proxmox/vms/vm_id")

router.include_router(proxmox_vms_vm_id_delete_router,              prefix="/v0/proxmox/vms/vm_id")
router.include_router(proxmox_vms_vm_id_clone_router,               prefix="/v0/proxmox/vms/vm_id")
router.include_router(proxmox_vms_vm_id_create_router,              prefix="/v0/proxmox/vms/vm_id")

router.include_router(proxmox_vms_vm_id_vm_get_config_router,       prefix="/v0/proxmox/vms/vm_id/config")
router.include_router(proxmox_vms_vm_id_vm_get_config_cdrom_router, prefix="/v0/proxmox/vms/vm_id/config")
router.include_router(proxmox_vms_vm_id_vm_get_config_cpu_router,   prefix="/v0/proxmox/vms/vm_id/config")
router.include_router(proxmox_vms_vm_id_vm_get_config_ram_router,   prefix="/v0/proxmox/vms/vm_id/config")
router.include_router(proxmox_vms_vm_id_vm_set_tags_router,         prefix="/v0/proxmox/vms/vm_id/config")


router.include_router(proxmox_vms_vm_id_vm_snapshot_create_router, prefix="/v0/proxmox/vms/vm_id/snapshot")
router.include_router(proxmox_vms_vm_id_vm_snapshot_list_router,   prefix="/v0/proxmox/vms/vm_id/snapshot")
router.include_router(proxmox_vms_vm_id_vm_snapshot_delete_router, prefix="/v0/proxmox/vms/vm_id/snapshot")
router.include_router(proxmox_vms_vm_id_vm_snapshot_revert_router, prefix="/v0/proxmox/vms/vm_id/snapshot")


# /v0/proxmox/storage/ - issue #17
router.include_router(proxmox_storage_with_storage_name_list_iso_router,      prefix="/v0/proxmox/storage/storage_name")
router.include_router(proxmox_storage_with_storage_name_list_template_router, prefix="/v0/proxmox/storage/storage_name")

router.include_router(proxmox_storage_list_router,  prefix="/v0/proxmox/storage")
router.include_router(proxmox_storage_download_iso, prefix="/v0/proxmox/storage")


# /v0/proxmox/firewall/ - issue #13

#
#
#





# /v0/run/catalog/
router.include_router(actions_run_router,   prefix="/v0/run/actions")
router.include_router(scenarios_run_router, prefix="/v0/run/scenarios")

#
# temp notes :
#
#
#     GET     /v1/catalog/scenarios/list
#     GET     /v1/catalog/scenarios/{scenario_id}
#                                      ├─ GET /{scenario_id}/changelog
#                                      └─ GET /{scenario_id}/versions
#     ...
#
#     GET     /v1/catalog/actions/list
#     GET     /v1/catalog/actions/{action_id}
#                                      ├─ GET /{action_id}/changelog
#                                      └─ GET /{action_id}/versions
#     ...
#     POST    /v1/runs/scenarios/{scenario_id}
#     POST    /v1/runs/actions/{action_id}g/scenar
#
#
#
#
#
#
#
# proxmox_vm.jsons.clone.to.jsons.sh
# proxmox_vm.jsons.create.to.jsons.sh
# proxmox_vm.jsons.set_tag.to.jsons.sh
# proxmox_vm.list.to.jsons.sh
# proxmox_vm.list_paused.to.jsons.sh
# proxmox_vm.list_running.to.jsons.sh
# proxmox_vm.list_running_and_extract_vm_id.to.text.sh
# proxmox_vm.list_stopped.to.jsons.sh
# proxmox_vm.list_vm_usage.to.jsons.sh
#
# proxmox_vm.pause_all_vms.to.jsons.sh
# proxmox_vm.resume_all_vms.to.jsons.sh
# proxmox_vm.start_all_vms.to.jsons.sh
# proxmox_vm.stop_all_vms.to.jsons.sh
# proxmox_vm.stop_force_all_vms.to.jsons.sh
#
# proxmox_vm.vm_id.delete.to.jsons.sh
# proxmox_vm.vm_id.get_config.to.jsons.sh
# proxmox_vm.vm_id.get_config_cdrom.to.jsons.sh
# proxmox_vm.vm_id.get_config_cpu.to.jsons.sh
# proxmox_vm.vm_id.get_config_ram.to.jsons.sh
# proxmox_vm.vm_id.get_usage.to.jsons.sh
# proxmox_vm.vm_id.list_vm_and_extract_vm_name.to.jsons.sh
#
#
# proxmox_vm.vm_id.pause.to.jsons.sh          - call ok
# proxmox_vm.vm_id.resume.to.jsons.sh         - call ok
# proxmox_vm.vm_id.start.to.jsons.sh          - call ok
# proxmox_vm.vm_id.stop.to.jsons.sh           - call ok
# proxmox_vm.vm_id.stop_force.to.jsons.sh     - call ok

