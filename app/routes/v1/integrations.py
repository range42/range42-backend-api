"""Optional installation integrations never grant environment readiness."""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.errors import Range42Error
from app.core.identity_runtime import identity_configuration
from app.schemas.v1.identities import IdentityIntegrationCapabilities

router = APIRouter(prefix='/integrations', tags=['v1 integrations'])


@router.get('/identity', response_model=IdentityIntegrationCapabilities)
async def identity_capability(request: Request):
    principal = getattr(request.state, 'principal', None)
    if principal is None or principal.role != 'admin':
        raise Range42Error(status=403, code='ACCESS_DENIED',
            message='Integration settings require a backend administrator.')
    state, _ = identity_configuration()
    value = IdentityIntegrationCapabilities(configuration_state=state)
    return JSONResponse(value.model_dump(), headers={'Cache-Control': 'no-store', 'Pragma': 'no-cache'})
