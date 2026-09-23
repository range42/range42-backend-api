from fastapi import APIRouter

from app.routes.v1.projects.crud import router as crud_router
from app.routes.v1.projects.compose import router as compose_router

router = APIRouter(prefix="/projects", tags=["v1 projects"])
router.include_router(crud_router)
router.include_router(compose_router)
