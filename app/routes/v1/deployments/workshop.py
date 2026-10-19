"""Run-scoped workshop projections within the normal deployment lifecycle."""
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict
from typing import Literal
import json
from fastapi.responses import Response
from sqlalchemy import select, or_

from app.core.db import get_session_factory
from app.core.errors import Range42Error
from app.core.models import Deployment, Attempt
from app.core.access import Principal
from app.core.workshop_models import WorkshopRun

router = APIRouter()


@router.get('/{deployment_id}/workshop')
async def status(deployment_id: str, request: Request):
    async with get_session_factory()() as session:
        deployment = await session.get(Deployment, deployment_id)
        run = await session.get(WorkshopRun, deployment_id)
        if not deployment or not run:
            raise Range42Error(status=404, code='WORKSHOP_NOT_FOUND', message='This deployment has no independent workshop run.')
        plan = deployment.native['workshop_plan']
        participants = []
        for person in plan['participants']:
            latest = await session.scalar(select(Attempt).where(Attempt.deployment_id == deployment_id,
                Attempt.scope.in_({'full', 'configure', 'reset', 'teardown', 'deploy_vms', 'delete_vms'}),
                or_(Attempt.operation['kind'].as_string().is_(None),
                    Attempt.operation['kind'].as_string() != 'workshop',
                    Attempt.operation['seat'].as_integer() == person['seat']))
                .order_by(Attempt.started_at.desc(), Attempt.id.desc()).limit(1))
            state = 'not_created'
            if latest:
                state = latest.state
                if latest.state == 'succeeded':
                    state = 'retired' if latest.scope in {'teardown', 'delete_vms'} else 'prepared'
            participants.append({**person, 'state': state, 'attempt_id': latest.id if latest else None})
        states = {person['state'] for person in participants}
        phase = {'not_created': 'draft', 'prepared': 'prepared', 'retired': 'retired'}.get(next(iter(states))) if len(states) == 1 else None
        if phase is None:
            phase = 'preparing' if states & {'pending', 'running', 'deploying', 'running_attempt'} else 'partial'
        principal = getattr(request.state, 'principal', Principal('development', 'admin'))
        return {'deployment_id': deployment_id, 'namespace': plan['namespace'],
                'phase': phase, 'ready': False, 'expires_at': plan['expires_at'],
                'participants': participants, 'capacity': plan['capacity'],
                'permissions': {'manage_instances': principal.role in {'admin', 'operator'},
                                'download_credentials': principal.role == 'admin'},
                'identity': {'mode': 'instance-local', 'profile': plan['identity_profile']},
                'access': {'state': 'not_configured', 'profile': plan['access_profile']}}


class ParticipantAction(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    action: Literal['deploy', 'configure', 'reset', 'teardown']
    confirmation: str | None = None


@router.post('/{deployment_id}/workshop/participants/{username}/actions', status_code=201)
async def participant_action(deployment_id: str, username: str, request: ParticipantAction):
    from app.routes.v1.deployments.attempts import reserve_attempt
    from app.schemas.v1.deployments import AttemptCreate, AttemptOut
    async with get_session_factory()() as session:
        deployment = await session.get(Deployment, deployment_id)
        plan = deployment.native.get('workshop_plan') if deployment and deployment.native else None
        participant = next((item for item in plan['participants'] if item['username'] == username), None) if plan else None
        if not participant:
            raise Range42Error(status=404, code='WORKSHOP_PARTICIPANT_NOT_FOUND', message='The participant does not belong to this workshop run.')
        if request.action in {'reset', 'teardown'} and request.confirmation != username:
            raise Range42Error(status=422, code='WORKSHOP_CONFIRMATION_REQUIRED', message='Confirm the selected participant before replacing or deleting its environment.')
        if request.action not in {'reset', 'teardown'} and request.confirmation is not None:
            raise Range42Error(status=422, code='WORKSHOP_CONFIRMATION_INVALID', message='This action does not require a destructive confirmation.')
        attempt = await reserve_attempt(deployment_id, AttemptCreate(
            scope='full' if request.action == 'deploy' else request.action,
            workshop_seat=participant['seat'], confirm_codename=deployment.codename), session)
        return AttemptOut.model_validate(attempt, from_attributes=True)


@router.get('/{deployment_id}/workshop/participants/{username}/handout')
async def handout(deployment_id: str, username: str):
    from app.core.workshop_handouts import participant_pdf
    async with get_session_factory()() as session:
        deployment = await session.get(Deployment, deployment_id)
        run = await session.get(WorkshopRun, deployment_id)
        plan = deployment.native.get('workshop_plan') if deployment and deployment.native else None
        participant = next((item for item in plan['participants'] if item['username'] == username), None) if plan else None
        if not participant or not run:
            raise Range42Error(status=404, code='WORKSHOP_PARTICIPANT_NOT_FOUND', message='The participant does not belong to this workshop run.')
        material = json.loads(run.credentials)[username]
        content = participant_pdf(plan, participant, material)
        return Response(content=content, media_type='application/pdf', headers={
            'Cache-Control': 'no-store', 'Pragma': 'no-cache', 'X-Content-Type-Options': 'nosniff',
            'Content-Disposition': f'attachment; filename="{plan["namespace"]}-{username}.pdf"'})
