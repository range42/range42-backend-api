from fastapi import APIRouter

from app.routes.v1.deployments.attempts import router as attempts_router
from app.routes.v1.deployments.crud import router as crud_router

router = APIRouter(prefix="/deployments", tags=["v1 deployments"])
router.include_router(crud_router)
router.include_router(attempts_router)
