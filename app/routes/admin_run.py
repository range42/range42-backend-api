



from fastapi import APIRouter

#
# runner routes - generic
#
from app.routes.v0.run.actions.actions_run                           import router as actions_run_router
from app.routes.v0.run.scenarios.scenarios_run                       import router as scenarios_run_router

#### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####

router = APIRouter()

#
# /v0/run/catalog/       - issue #15
#
router.include_router(actions_run_router,   prefix="/v0/admin/run/actions")
router.include_router(scenarios_run_router, prefix="/v0/admin/run/scenarios")

