from fastapi import APIRouter

from app.routes.v1.catalog.sources import router as sources_router
from app.routes.v1.catalog.refresh import router as refresh_router

router = APIRouter(prefix="/catalog", tags=["v1 catalog"])
router.include_router(sources_router)
router.include_router(refresh_router)
