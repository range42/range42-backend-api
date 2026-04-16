from fastapi import APIRouter

from app.routes.v1.admin.retention import router as retention_router
from app.routes.v1.admin.stats import router as stats_router

router = APIRouter(prefix="/admin", tags=["v1 admin"])
router.include_router(stats_router)
router.include_router(retention_router)
