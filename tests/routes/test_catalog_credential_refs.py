"""Real local Git transport verifies reference/cache/metadata boundaries."""
from urllib.parse import unquote, urlsplit

import git
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.models import Source
from tests.routes.test_catalog_sources import _boot


@pytest.fixture
async def reference_catalog(tmp_path, monkeypatch):
    monkeypatch.setenv('RANGE42_GIT_SECRET_ENV_ALLOWLIST', 'R42_TEST_PAT')
    monkeypatch.setenv('R42_TEST_PAT', 'first-secret')
    root = tmp_path / 'private'
    root.mkdir(mode=0o700)
    monkeypatch.setenv('RANGE42_GIT_SECRET_DIR', str(root))
    token_file = root / 'token'
    token_file.write_text('first-secret\n')
    token_file.chmod(0o600)
    tree = tmp_path / 'catalog'
    tree.mkdir()
    (tree / 'example').mkdir()
    (tree / 'example/range42.yaml').write_text('kind: lab\nname: Example\n')
    local = git.Repo.init(tree, initial_branch='main')
    local.index.add(['example/range42.yaml'])
    actor = git.Actor('Test', 'test@example.invalid')
    local.index.commit('Fixture', author=actor, committer=actor)
    original = git.Repo.clone_from
    calls = []

    def clone(url, dest, **kwargs):
        calls.append(unquote(urlsplit(url).password or ''))
        return original(str(tree), dest, **kwargs)

    monkeypatch.setattr(git.Repo, 'clone_from', clone)
    app, dbmod = await _boot(tmp_path, monkeypatch)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://t') as client:
            response = await client.post('/v1/catalog/sources', json={
                'provider': 'github', 'base_url': 'https://github.com', 'auth_kind': 'pat',
                'token_ref': 'env://R42_TEST_PAT', 'repos': [{'owner': 'owner', 'repo': 'catalog'}],
            })
            assert response.status_code == 201
            yield client, app, dbmod, response.json(), calls, token_file
    finally:
        local.close()
        await dbmod.dispose_engine()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['env', 'file'])
async def test_rotation_bypasses_old_snapshot_without_expiry_or_refresh(reference_catalog, monkeypatch, kind):
    client, app, dbmod, source, calls, path = reference_catalog
    reference = path.as_uri() if kind == 'file' else 'env://R42_TEST_PAT'
    assert (await client.patch('/v1/catalog/sources/' + source['id'], json={'auth_kind': 'pat', 'token_ref': reference})).status_code == 200
    first = await client.get('/v1/catalog/entries')
    assert first.status_code == 200 and first.json()['total'] == 1
    assert (await client.get('/v1/catalog/entries')).status_code == 200
    assert calls == ['first-secret']
    if kind == 'file':
        path.write_text('second-secret\n')
    else:
        monkeypatch.setenv('R42_TEST_PAT', 'second-secret')
    assert (await client.get('/v1/catalog/entries')).status_code == 200
    assert calls == ['first-secret', 'second-secret']
    async with dbmod.get_session_factory()() as session:
        assert (await session.get(Source, source['id'])).token_ref == reference
        stored = await session.scalar(text('SELECT token_ref FROM sources WHERE id=:id'), {'id': source['id']})
        assert reference not in stored and 'first-secret' not in stored and 'second-secret' not in stored
    assert 'secret' not in first.text


@pytest.mark.asyncio
async def test_cache_key_and_checkout_keep_one_resolved_value_across_rotation(reference_catalog, monkeypatch):
    from app.core.catalog_snapshots import CatalogSnapshots
    client, _app, _dbmod, _source, calls, _path = reference_catalog
    original = CatalogSnapshots.get

    async def rotate_between_key_and_load(self, key, loader):
        monkeypatch.setenv('R42_TEST_PAT', 'second-secret')
        return await original(self, key, loader)

    monkeypatch.setattr(CatalogSnapshots, 'get', rotate_between_key_and_load)
    assert (await client.get('/v1/catalog/entries')).status_code == 200
    assert calls == ['first-secret']
    assert (await client.get('/v1/catalog/entries')).status_code == 200
    assert calls == ['first-secret', 'second-secret']


@pytest.mark.asyncio
async def test_missing_reference_refuses_cached_read_and_refresh_but_metadata_is_editable(reference_catalog, monkeypatch):
    client, _app, _dbmod, source, calls, _path = reference_catalog
    assert (await client.get('/v1/catalog/entries')).status_code == 200
    monkeypatch.delenv('R42_TEST_PAT')
    for method, path in [('get', '/v1/catalog/entries'), ('post', f"/v1/catalog/sources/{source['id']}/refresh")]:
        response = await getattr(client, method)(path)
        assert response.status_code == 503
        assert response.json()['code'] == 'GIT_CREDENTIAL_REFERENCE_UNAVAILABLE'
        assert 'R42_TEST_PAT' not in response.text and 'first-secret' not in response.text
    assert calls == ['first-secret']
    metadata = await client.get('/v1/catalog/sources')
    assert metadata.status_code == 200
    assert metadata.json()['items'][0]['has_token'] is True
    assert 'token_ref' not in metadata.text and 'R42_TEST_PAT' not in metadata.text
    assert (await client.patch('/v1/catalog/sources/' + source['id'], json={'auth_kind': 'pat', 'token_ref': 'replacement'})).status_code == 200


@pytest.mark.asyncio
async def test_refresh_detail_and_bundle_use_resolved_token(reference_catalog, monkeypatch):
    from app.routes.v1.catalog import bundles
    client, _app, _dbmod, source, calls, _path = reference_catalog
    assert (await client.post(f"/v1/catalog/sources/{source['id']}/refresh")).status_code == 200
    assert (await client.get(f"/v1/catalog/entries/{source['id']}/example")).status_code == 200
    assert calls == ['first-secret', 'first-secret']
    seen = []

    def checkout(**kwargs):
        seen.append(kwargs['token'])
        return kwargs['dest']

    monkeypatch.setattr(bundles, 'checkout_repository', checkout)
    monkeypatch.setattr(bundles, 'resolve_bundle', lambda *args, **kwargs: {'resolved': True})
    response = await client.post(f"/v1/catalog/sources/{source['id']}/bundles/resolve", json={
        'path': 'bundles/generic/demo', 'sha': 'a' * 40, 'target_kind': 'VM',
    })
    assert response.status_code == 200
    assert seen == ['first-secret']


@pytest.mark.asyncio
async def test_upstream_error_cannot_echo_resolved_secret_or_reference(reference_catalog, monkeypatch, caplog):
    client, _app, _dbmod, source, _calls, _path = reference_catalog

    def fail(*args, **kwargs):
        raise RuntimeError('remote echoed first-secret and env://R42_TEST_PAT')

    monkeypatch.setattr(git.Repo, 'clone_from', fail)
    for method, path in [('get', '/v1/catalog/entries'), ('post', f"/v1/catalog/sources/{source['id']}/refresh")]:
        response = await getattr(client, method)(path)
        assert response.status_code == 502
        assert 'first-secret' not in response.text and 'R42_TEST_PAT' not in response.text
    assert 'first-secret' not in caplog.text
