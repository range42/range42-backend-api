from fastapi import APIRouter

#
# runner routes - bundles/core
#
from app.routes.v0.admin.bundles.core.linux.ubuntu.install.dot_files  import router as bundles_core_linux_ubuntu_install_dotfiles_router
from app.routes.v0.admin.bundles.core.linux.ubuntu.configure.add_user import router as bundles_core_linux_ubuntu_configure_add_user_router

#### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####

router = APIRouter()


#
# /v0/admin/run/bundles/core/linux/ubuntu/install/
#
router.include_router(bundles_core_linux_ubuntu_install_dotfiles_router,   prefix="/v0/admin/run/actions")   #ISSUE-28
router.include_router(bundles_core_linux_ubuntu_configure_add_user_router,   prefix="/v0/admin/run/actions") #ISSUE-19
