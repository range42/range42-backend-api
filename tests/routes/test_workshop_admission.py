import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_admission_is_admin_only_and_a_typed_private_projection(monkeypatch):
    from app.core.access import Principal, allowed
    from app.main import create_app
    from app.routes.v1.admin import workshop

    for role in ['viewer', 'operator']:
        assert not allowed(Principal('staff', role), 'POST', '/v1/admin/workshop/admission')
        assert not allowed(Principal('staff', role), 'GET', '/v1/admin/workshop/admission')
    calls = []
    async def agent(value):
        calls.append(value)
        return {'scope': 'legacy_controller_queue', 'state': 'paused', 'revision': 1,
                'pause_id': 'a' * 32, 'queued_count': 0, 'running_count': 0, 'secret': 'DO-NOT-RETURN'}
    monkeypatch.setattr(workshop, 'agent_request', agent)
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url='http://test') as client:
        response = await client.get('/v1/admin/workshop/admission')
    assert response.status_code == 200, response.text
    assert calls == [{'action': 'admission'}]
    assert 'DO-NOT-RETURN' not in response.text
    assert response.headers['cache-control'] == 'no-store'


@pytest.mark.asyncio
async def test_only_reviewed_pause_or_confirmed_resume_reaches_controller(monkeypatch):
    from app.main import create_app
    from app.routes.v1.admin import workshop
    calls = []
    async def agent(value):
        calls.append(value)
        return {'scope': 'legacy_controller_queue', 'state': 'paused', 'revision': 1,
                'pause_id': 'a' * 32, 'queued_count': 0, 'running_count': 0}
    monkeypatch.setattr(workshop, 'agent_request', agent)
    pause = {'action': 'pause', 'expected_revision': 0, 'request_id': 'a' * 32}
    resume = {'action': 'resume', 'expected_revision': 1, 'request_id': 'b' * 32, 'pause_id': 'a' * 32}
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url='http://test') as client:
        for payload in [
            {**pause, 'expected_revision': True}, {**pause, 'command': 'id'},
            {**pause, 'pause_id': 'a' * 32}, {**pause, 'confirmation': 'RESUME'},
            {**pause, 'request_id': '../foo'}, resume, {**resume, 'confirmation': 'yes'},
            {**resume, 'pause_id': None, 'confirmation': 'RESUME'},
        ]:
            assert (await client.post('/v1/admin/workshop/admission', json=payload)).status_code == 422
        assert not calls
        response = await client.post('/v1/admin/workshop/admission', json=pause)
        assert response.status_code == 200, response.text
        assert calls == [pause]
        response = await client.post('/v1/admin/workshop/admission', json={**resume, 'confirmation': 'RESUME'})
        assert response.status_code == 200, response.text
        assert calls[-1] == resume


def test_controller_rejections_are_sanitized_actionable_errors():
    from app.core.errors import Range42Error
    from app.routes.v1.admin import workshop
    assert hasattr(workshop, 'agent_result'), 'The wire protocol must distinguish a pause from unavailable service'
    for code, status in [('WORKSHOP_PAUSED', 423), ('WORKSHOP_ADMISSION_CHANGED', 409), ('WORKSHOP_DRAINING', 409)]:
        with pytest.raises(Range42Error) as error:
            workshop.agent_result({'ok': False, 'code': code, 'error': 'PRIVATE diagnostic'})
        assert error.value.code == code and error.value.status == status
        assert 'PRIVATE' not in str(error.value)
    with pytest.raises(ValueError):
        workshop.agent_result({'ok': False, 'code': 'ARBITRARY', 'error': 'PRIVATE'})
