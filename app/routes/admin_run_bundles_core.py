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
router.include_router(bundles_core_linux_ubuntu_configure_add_user_router,   prefix="/v0/admin/run/bundles") #ISSUE-19
