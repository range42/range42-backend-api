from fastapi import APIRouter

#
# runner routes - bundles/core/linux/ubuntu/configure/*
#

from app.routes.v0.admin.bundles.core.linux.ubuntu.configure.add_user          import router as bundles_core_linux_ubuntu_configure_add_user_router

#### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####

#
# runner routes - bundles/core/linux/ubuntu/install/*
#
from app.routes.v0.admin.bundles.core.linux.ubuntu.install.dot_files           import router as bundles_core_linux_ubuntu_install_dotfiles_router
from app.routes.v0.admin.bundles.core.linux.ubuntu.install.docker_compose      import router as bundles_core_linux_ubuntu_install_docker_router
from app.routes.v0.admin.bundles.core.linux.ubuntu.install.docker              import router as bundles_core_linux_ubuntu_install_docker_compose_router
from app.routes.v0.admin.bundles.core.linux.ubuntu.install.basic_packages      import router as bundles_core_linux_ubuntu_install_basic_packages_router


#### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####

router = APIRouter()

#
# /v0/admin/run/bundles/core/linux/ubuntu/install/
#
router.include_router(bundles_core_linux_ubuntu_install_dotfiles_router,         prefix="/v0/admin/run/bundles")   #ISSUE-28
router.include_router(bundles_core_linux_ubuntu_install_docker_router,           prefix="/v0/admin/run/bundles")   #ISSUE-22
router.include_router(bundles_core_linux_ubuntu_install_docker_compose_router,   prefix="/v0/admin/run/bundles")   #ISSUE-21
router.include_router(bundles_core_linux_ubuntu_install_basic_packages_router,   prefix="/v0/admin/run/bundles")   #ISSUE-28

#
# /v0/admin/run/bundles/core/linux/ubuntu/configure/
#
router.include_router(bundles_core_linux_ubuntu_configure_add_user_router,       prefix="/v0/admin/run/bundles")   #ISSUE-19


#
# proxmox/configure/default/create-vms*
#

from app.routes.v0.admin.bundles.proxmox.configure.default.vms.create_vms_admin_default           import router as bundles_proxmox_configure_default_vms_create_admin_default_router   #
from app.routes.v0.admin.bundles.proxmox.configure.default.vms.create_vms_vuln_default            import router as bundles_proxmox_configure_default_vms_create_vuln_default_router    # ISSUE 30
from app.routes.v0.admin.bundles.proxmox.configure.default.vms.create_vms_student_default         import router as bundles_proxmox_configure_default_vms_create_student_default_router #
from app.routes.v0.admin.bundles.proxmox.configure.default.vms.start_stop_pause_resume_admin      import router as bundles_proxmox_configure_default_vms_start_stop_resume_pause_default_admin_router      #
from app.routes.v0.admin.bundles.proxmox.configure.default.vms.start_stop_pause_resume_vuln       import router as bundles_proxmox_configure_default_vms_start_stop_resume_pause_default_vuln_router      #
from app.routes.v0.admin.bundles.proxmox.configure.default.vms.start_stop_pause_resume_student    import router as bundles_proxmox_configure_default_vms_start_stop_resume_pause_default_student_router      #

router.include_router(bundles_proxmox_configure_default_vms_create_admin_default_router,            prefix="/v0/admin/run/bundles")           #
router.include_router(bundles_proxmox_configure_default_vms_create_vuln_default_router,             prefix="/v0/admin/run/bundles")           # ISSUE 30
router.include_router(bundles_proxmox_configure_default_vms_create_student_default_router,          prefix="/v0/admin/run/bundles")           #

# mass start, stop, pause, resume :: admin | student | vuln vms
router.include_router(bundles_proxmox_configure_default_vms_start_stop_resume_pause_default_admin_router,  prefix="/v0/admin/run/bundles")   #
router.include_router(bundles_proxmox_configure_default_vms_start_stop_resume_pause_default_vuln_router,   prefix="/v0/admin/run/bundles")   # ISSUE 30
router.include_router(bundles_proxmox_configure_default_vms_start_stop_resume_pause_default_student_router, prefix="/v0/admin/run/bundles")   #