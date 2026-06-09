from fastapi import APIRouter

from app.routes.v1.deployments.attempts import router as attempts_router
from app.routes.v1.deployments.crud import router as crud_router
from app.routes.v1.deployments.events import router as events_router
from app.routes.v1.deployments.preflight import router as preflight_router
from app.routes.v1.deployments.snapshots import router as snap_router
from app.routes.v1.deployments.team_actions import router as team_router
from app.routes.v1.deployments.teardown import router as teardown_router
from app.routes.v1.deployments.timings import router as timings_router

router = APIRouter(prefix="/deployments", tags=["v1 deployments"])
router.include_router(crud_router)
router.include_router(attempts_router)
router.include_router(events_router)
router.include_router(preflight_router)
router.include_router(teardown_router)
router.include_router(team_router)
router.include_router(snap_router)
router.include_router(timings_router)
