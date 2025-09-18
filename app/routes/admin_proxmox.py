from fastapi import APIRouter

#
# vm list and usage          - issue #2
#
from app.routes.v0.proxmox.vms.list                                  import router as proxmox_vms_list_router
from app.routes.v0.proxmox.vms.list_usage                            import router as proxmox_vms_list_usage_router

#
# proxmox vm life cycle mgmt - issue #3
#
from app.routes.v0.proxmox.vms.vm_id.start                           import router as proxmox_vms_vm_id_start
from app.routes.v0.proxmox.vms.vm_id.stop                            import router as proxmox_vms_vm_id_stop_router
from app.routes.v0.proxmox.vms.vm_id.pause                           import router as proxmox_vms_vm_id_pause_router
from app.routes.v0.proxmox.vms.vm_id.resume                          import router as proxmox_vms_vm_id_resume_router
from app.routes.v0.proxmox.vms.vm_id.stop_force                      import router as proxmox_vms_vm_id_stop_force_router

#
#vm create delete clone      - issue #3

from app.routes.v0.proxmox.vms.vm_id.create                          import router as proxmox_vms_vm_id_create_router
from app.routes.v0.proxmox.vms.vm_id.delete                          import router as proxmox_vms_vm_id_delete_router
from app.routes.v0.proxmox.vms.vm_id.clone                           import router as proxmox_vms_vm_id_clone_router

#
#config                      - issue #5
#
from app.routes.v0.proxmox.vms.vm_id.config.vm_get_config            import router as proxmox_vms_vm_id_vm_get_config_router
from app.routes.v0.proxmox.vms.vm_id.config.vm_get_config_cdrom      import router as proxmox_vms_vm_id_vm_get_config_cdrom_router
from app.routes.v0.proxmox.vms.vm_id.config.vm_get_config_cpu        import router as proxmox_vms_vm_id_vm_get_config_cpu_router
from app.routes.v0.proxmox.vms.vm_id.config.vm_get_config_ram        import router as proxmox_vms_vm_id_vm_get_config_ram_router
from app.routes.v0.proxmox.vms.vm_id.config.vm_set_tag               import router as proxmox_vms_vm_id_vm_set_tags_router

#
#snapshot                     - issue #9
#
from app.routes.v0.proxmox.vms.vm_id.snapshots.vm_create             import router as proxmox_vms_vm_id_vm_snapshot_create_router
from app.routes.v0.proxmox.vms.vm_id.snapshots.vm_list               import router as proxmox_vms_vm_id_vm_snapshot_list_router
from app.routes.v0.proxmox.vms.vm_id.snapshots.vm_delete             import router as proxmox_vms_vm_id_vm_snapshot_delete_router
from app.routes.v0.proxmox.vms.vm_id.snapshots.vm_revert             import router as proxmox_vms_vm_id_vm_snapshot_revert_router

#
# storage                     - issue #14
#
from app.routes.v0.proxmox.storage.storage_name.list_iso             import router as proxmox_storage_with_storage_name_list_iso_router
from app.routes.v0.proxmox.storage.storage_name.list_template        import router as proxmox_storage_with_storage_name_list_template_router

from app.routes.v0.proxmox.storage.list                              import router as proxmox_storage_list_router
from app.routes.v0.proxmox.storage.download_iso                      import router as proxmox_storage_download_iso

#
# firewall                    - issue #13
#
from app.routes.v0.proxmox.firewall.add_iptable_alias                import router as proxmox_firewall_add_iptables_alias_router
from app.routes.v0.proxmox.firewall.apply_iptables_rules             import router as proxmox_firewall_apply_iptables_rules_router

from app.routes.v0.proxmox.firewall.delete_iptables_alias            import router as proxmox_firewall_delete_iptables_alias_router
from app.routes.v0.proxmox.firewall.delete_iptables_rule             import router as proxmox_firewall_delete_iptables_rule_router

from app.routes.v0.proxmox.firewall.disable_firewall_dc              import router as proxmox_firewall_disable_firewall_dc_router
from app.routes.v0.proxmox.firewall.disable_firewall_node            import router as proxmox_firewall_disable_firewall_node_router
from app.routes.v0.proxmox.firewall.disable_firewall_vm              import router as proxmox_firewall_disable_firewall_vm_router

from app.routes.v0.proxmox.firewall.enable_firewall_dc               import router as proxmox_firewall_enable_firewall_dc_router
from app.routes.v0.proxmox.firewall.enable_firewall_node             import router as proxmox_firewall_enable_firewall_node_router
from app.routes.v0.proxmox.firewall.enable_firewall_vm               import router as proxmox_firewall_enable_firewall_vm_router

from app.routes.v0.proxmox.firewall.list_iptables_alias              import router as proxmox_firewall_list_iptables_alias_router
from app.routes.v0.proxmox.firewall.list_iptables_rules              import router as proxmox_firewall_list_iptables_rules_router


#
# network                     - issue #11
#
from app.routes.v0.proxmox.network.vm.add_network                    import router as proxmox_network_vm_add_network_router
from app.routes.v0.proxmox.network.vm.delete_network                 import router as proxmox_network_vm_delete_network_router
from app.routes.v0.proxmox.network.vm.list_network                   import router as proxmox_network_vm_list_network_router

from app.routes.v0.proxmox.network.node.add_network                  import router as proxmox_network_node_add_network_router
from app.routes.v0.proxmox.network.node.delete_network               import router as proxmox_network_node_delete_network_router
from app.routes.v0.proxmox.network.node.list_network                 import router as proxmox_network_node_list_network_router


#### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####
#### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####


router = APIRouter()


#
# /v0/admin/proxmox/vms        - issue #3
#
router.include_router(proxmox_vms_list_router,                                 prefix="/v0/admin/proxmox/vms")
router.include_router(proxmox_vms_list_usage_router,                           prefix="/v0/admin/proxmox/vms")

#
# /v0/admin/proxmox/vms/vm_id/ - issue #3
#
router.include_router(proxmox_vms_vm_id_start,                                 prefix="/v0/admin/proxmox/vms/vm_id")
router.include_router(proxmox_vms_vm_id_stop_router,                           prefix="/v0/admin/proxmox/vms/vm_id")
router.include_router(proxmox_vms_vm_id_stop_force_router,                     prefix="/v0/admin/proxmox/vms/vm_id")
router.include_router(proxmox_vms_vm_id_pause_router,                          prefix="/v0/admin/proxmox/vms/vm_id")
router.include_router(proxmox_vms_vm_id_resume_router,                         prefix="/v0/admin/proxmox/vms/vm_id")

#
# /v0/admin/proxmox/firewall/  - issue #6
#
router.include_router(proxmox_vms_vm_id_create_router,                         prefix="/v0/admin/proxmox/vms/vm_id")
router.include_router(proxmox_vms_vm_id_clone_router,                          prefix="/v0/admin/proxmox/vms/vm_id")
router.include_router(proxmox_vms_vm_id_delete_router,                         prefix="/v0/admin/proxmox/vms/vm_id")

#
# /v0/admin/proxmox/firewall/  - issue #5
#
router.include_router(proxmox_vms_vm_id_vm_get_config_router,                  prefix="/v0/admin/proxmox/vms/vm_id/config")
router.include_router(proxmox_vms_vm_id_vm_get_config_cdrom_router,            prefix="/v0/admin/proxmox/vms/vm_id/config")
router.include_router(proxmox_vms_vm_id_vm_get_config_cpu_router,              prefix="/v0/admin/proxmox/vms/vm_id/config")
router.include_router(proxmox_vms_vm_id_vm_get_config_ram_router,              prefix="/v0/admin/proxmox/vms/vm_id/config")

#
# /v0/admin/proxmox/firewall/  - issue #7
#
router.include_router(proxmox_vms_vm_id_vm_set_tags_router,                    prefix="/v0/admin/proxmox/vms/vm_id/config")

#
# /v0/admin/proxmox/firewall/  - issue #9
#
router.include_router(proxmox_vms_vm_id_vm_snapshot_list_router,               prefix="/v0/admin/proxmox/vms/vm_id/snapshot")
router.include_router(proxmox_vms_vm_id_vm_snapshot_create_router,             prefix="/v0/admin/proxmox/vms/vm_id/snapshot")
router.include_router(proxmox_vms_vm_id_vm_snapshot_delete_router,             prefix="/v0/admin/proxmox/vms/vm_id/snapshot")
router.include_router(proxmox_vms_vm_id_vm_snapshot_revert_router,             prefix="/v0/admin/proxmox/vms/vm_id/snapshot")

#
# /v0/admin/proxmox/storage/   - issue #17
#
router.include_router(proxmox_storage_with_storage_name_list_iso_router,       prefix="/v0/admin/proxmox/storage/storage_name")
router.include_router(proxmox_storage_with_storage_name_list_template_router,  prefix="/v0/admin/proxmox/storage/storage_name")

router.include_router(proxmox_storage_list_router,                             prefix="/v0/admin/proxmox/storage")
router.include_router(proxmox_storage_download_iso,                            prefix="/v0/admin/proxmox/storage")

#
# /v0/admin/proxmox/firewall/  - issue #13, #12
#
router.include_router(proxmox_firewall_list_iptables_alias_router,             prefix="/v0/admin/proxmox/firewall")
router.include_router(proxmox_firewall_add_iptables_alias_router,              prefix="/v0/admin/proxmox/firewall")
router.include_router(proxmox_firewall_delete_iptables_alias_router,           prefix="/v0/admin/proxmox/firewall")

router.include_router(proxmox_firewall_list_iptables_rules_router,             prefix="/v0/admin/proxmox/firewall")
router.include_router(proxmox_firewall_apply_iptables_rules_router,            prefix="/v0/admin/proxmox/firewall")
router.include_router(proxmox_firewall_delete_iptables_rule_router,            prefix="/v0/admin/proxmox/firewall")

router.include_router(proxmox_firewall_enable_firewall_vm_router,              prefix="/v0/admin/proxmox/firewall")
router.include_router(proxmox_firewall_disable_firewall_vm_router,             prefix="/v0/admin/proxmox/firewall")

router.include_router(proxmox_firewall_enable_firewall_node_router,            prefix="/v0/admin/proxmox/firewall")
router.include_router(proxmox_firewall_disable_firewall_node_router,           prefix="/v0/admin/proxmox/firewall")

router.include_router(proxmox_firewall_enable_firewall_dc_router,              prefix="/v0/admin/proxmox/firewall")
router.include_router(proxmox_firewall_disable_firewall_dc_router,             prefix="/v0/admin/proxmox/firewall")

#
# /v0/admin/proxmox/firewall/  - issue #10, #11
#
router.include_router(proxmox_network_vm_add_network_router,                   prefix="/v0/admin/proxmox/network")
router.include_router(proxmox_network_vm_delete_network_router,                prefix="/v0/admin/proxmox/network")
router.include_router(proxmox_network_vm_list_network_router,                  prefix="/v0/admin/proxmox/network")

router.include_router(proxmox_network_node_add_network_router,                 prefix="/v0/admin/proxmox/network")
router.include_router(proxmox_network_node_delete_network_router,              prefix="/v0/admin/proxmox/network")
router.include_router(proxmox_network_node_list_network_router,                prefix="/v0/admin/proxmox/network")
