import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_deployment_creation_reaches_validation_without_an_http_redirect():
    from app.main import create_app
    async with AsyncClient(transport=ASGITransport(app=create_app()),base_url='http://test',follow_redirects=False) as client:
        response=await client.post('/v1/deployments',json={})
        assert response.status_code==422
        assert 'location' not in response.headers


@pytest.mark.asyncio
async def test_deployment_list_accepts_both_path_spellings_without_redirecting():
    from app.main import create_app
    async with AsyncClient(transport=ASGITransport(app=create_app()),base_url='http://test',follow_redirects=False) as client:
        response=await client.get('/v1/deployments')
        assert response.status_code==200
        assert 'location' not in response.headers
