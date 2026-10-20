"""Administrator onboarding inside the saved native deployment workflow."""
import hashlib
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.core.db import get_session_factory
from app.core.errors import Range42Error
from app.core.identity_runtime import identity_request, unavailable
from app.core.models import Deployment
from app.schemas.v1.identities import IdentityAction, IdentityState, RegisterScope, RunIdentities, SetMembership

router = APIRouter()


async def saved_run(deployment_id):
    async with get_session_factory()() as session:
        deployment = await session.get(Deployment, deployment_id)
        plan = deployment.native.get('workshop_plan') if deployment and deployment.native else None
        if not isinstance(plan, dict):
            raise Range42Error(status=404, code='IDENTITY_RUN_NOT_FOUND',
                message='This deployment has no saved run access plan.')
        try:
            expiry = datetime.fromisoformat(plan['expires_at'])
            if expiry.tzinfo is None:
                raise ValueError()
            teams = [row['username'] for row in plan['participants']]
            revision = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            return {'deployment_id': deployment_id, 'expires_at': plan['expires_at'],
                    'teams': teams, 'run_revision': revision}
        except (KeyError, TypeError, ValueError):
            raise Range42Error(status=409, code='IDENTITY_RUN_PLAN_INVALID',
                message='The saved run access plan requires operator inspection.') from None


def actor(request):
    principal = getattr(request.state, 'principal', None)
    if principal is None or principal.role != 'admin':
        raise Range42Error(status=403, code='ACCESS_DENIED', message='Identity management requires a backend administrator.')
    return principal.actor_id


def response(plan, value):
    try:
        state = IdentityState.model_validate(value)
    except ValidationError:
        raise unavailable() from None
    return JSONResponse({**plan, 'state': state.model_dump()}, headers={'Cache-Control': 'no-store', 'Pragma': 'no-cache'})


@router.get('/{deployment_id}/identities', response_model=RunIdentities)
async def status(deployment_id: str, request: Request):
    name = actor(request)
    plan = await saved_run(deployment_id)
    value = await identity_request({'version': 1, 'action': 'status', 'actor': name})
    return response(plan, value)


@router.post('/{deployment_id}/identities', response_model=RunIdentities)
async def apply(deployment_id: str, payload: IdentityAction, request: Request):
    name = actor(request)
    plan = await saved_run(deployment_id)
    if plan['run_revision'] != payload.run_revision:
        raise Range42Error(status=409, code='IDENTITY_RUN_REVIEW_CHANGED',
            message='The saved run changed after review. Refresh before changing its people or memberships.')
    operation = payload.operation.model_dump()
    if isinstance(payload.operation, SetMembership):
        team = payload.operation.team_ref
        if ((payload.operation.role == 'participant' and team is None)
                or (team is not None and team not in plan['teams'])):
            raise Range42Error(status=422, code='IDENTITY_TEAM_INVALID',
                message='Choose a participant seat from this saved run.')
    if isinstance(payload.operation, RegisterScope):
        if datetime.fromisoformat(plan['expires_at']) <= datetime.now(timezone.utc):
            raise Range42Error(status=409, code='IDENTITY_RUN_EXPIRED',
                message='This run has expired. Create a new run to grant new access.')
        operation['expires_at'] = plan['expires_at']
        operation['participant_limit_per_team'] = 1
    operation['scope_ref'] = deployment_id
    value = await identity_request({'version': 1, 'action': 'apply', 'actor': name, 'request': {
        'version': 1, 'registry_id': payload.registry_id, 'expected_revision': payload.expected_revision, 'operation': operation}})
    return response(plan, value.get('state'))
