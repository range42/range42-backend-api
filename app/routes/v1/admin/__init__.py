from fastapi import APIRouter

from app.routes.v1.admin.stats import router as stats_router

router = APIRouter(prefix="/admin", tags=["v1 admin"])
router.include_router(stats_router)
