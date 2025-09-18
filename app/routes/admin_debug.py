


from fastapi import APIRouter

#
# debug routes
#
from app.routes.v0.debug.ping import router as debug_ping
from app.routes.v0.debug._test_func import router as debug_test_func

#### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####

router = APIRouter()

#
# temp
router.include_router(debug_test_func, prefix="/v0/admin/debug")

#
# /v0/admin/debug/             - issue #1
#
router.include_router(debug_ping, prefix="/v0/admin/debug")