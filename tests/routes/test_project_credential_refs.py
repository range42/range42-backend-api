"""Pinned checkout and its runner retain exactly the resolved credential."""
import asyncio
import json
from urllib.parse import quote

import pytest

from app.core.errors import ProjectCheckoutError
from app.core.models import Attempt, Deployment, Source
from app.core.scenario import prepare_project_scenario
from tests.fixtures.fake_runner import FakeRunner
from tests.routes.test_project_scenario_execution import _boot, seed_scenario


@pytest.mark.asyncio
async def test_checkout_reference_rotating_after_fetch_keeps_original_runner_redaction(tmp_path, monkeypatch):
    from app.core import scenario
    from app.core.deploy_trigger import _BACKGROUND_TASKS, start_attempt
    monkeypatch.setenv('RANGE42_GIT_SECRET_ENV_ALLOWLIST', 'R42_TEST_PAT')
    monkeypatch.setenv('R42_TEST_PAT', 'checkout-first:/@secret')
    _app, dbmod = await _boot(tmp_path, monkeypatch)
    seen, redactions = [], []
    original = scenario.checkout_repository

    def checkout(**kwargs):
        seen.append(kwargs['token'])
        result = original(**kwargs)
        monkeypatch.setenv('R42_TEST_PAT', 'replacement-secret')
        return result

    monkeypatch.setattr(scenario, 'checkout_repository', checkout)

    class RecordingRunner(FakeRunner):
        async def start(self, **kwargs):
            redactions.extend(json.loads((kwargs['private_data_dir'] / 'redaction.json').read_text()))
            return await super().start(**kwargs)

    try:
        await seed_scenario(dbmod, tmp_path)
        async with dbmod.get_session_factory()() as session:
            source = await session.get(Source, 's')
            source.auth_kind = 'pat'
            source.token_ref = 'env://R42_TEST_PAT'
            session.add(Attempt(id='reference-attempt', deployment_id='dep-1', scope='full', state='pending'))
            await session.commit()
            await start_attempt(session, attempt=await session.get(Attempt, 'reference-attempt'), runner=RecordingRunner(script=[]))
        await asyncio.gather(*list(_BACKGROUND_TASKS))
        assert seen == ['checkout-first:/@secret']
        assert 'checkout-first:/@secret' in redactions
        assert quote('checkout-first:/@secret', safe='') in redactions
        assert 'replacement-secret' not in redactions
    finally:
        await asyncio.gather(*list(_BACKGROUND_TASKS), return_exceptions=True)
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_project_checkout_missing_reference_refuses_before_git(tmp_path, monkeypatch):
    from app.core import scenario
    from app.core.errors import Range42Error
    _app, dbmod = await _boot(tmp_path, monkeypatch)
    monkeypatch.setenv('RANGE42_GIT_SECRET_ENV_ALLOWLIST', 'R42_TEST_PAT')
    monkeypatch.delenv('R42_TEST_PAT', raising=False)
    try:
        ws, _sha = await seed_scenario(dbmod, tmp_path)
        async with dbmod.get_session_factory()() as session:
            source = await session.get(Source, 's')
            source.auth_kind = 'pat'
            source.token_ref = 'env://R42_TEST_PAT'
            await session.commit()
            monkeypatch.setattr(scenario, 'checkout_repository', lambda **kwargs: pytest.fail('Git must not run'))
            with pytest.raises(Range42Error) as caught:
                await prepare_project_scenario(session, await session.get(Deployment, 'dep-1'), dest=ws / 'checkout')
            assert caught.value.code == 'GIT_CREDENTIAL_REFERENCE_UNAVAILABLE'
    finally:
        await dbmod.dispose_engine()


def test_git_failure_does_not_return_remote_echoed_raw_or_url_encoded_credential(monkeypatch, tmp_path):
    import subprocess
    from app.core.project import checkout_repository

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(128, args[0], stderr='echoed raw-secret and raw%2Dsecret env://PRIVATE_NAME')

    monkeypatch.setattr(subprocess, 'run', fail)
    with pytest.raises(ProjectCheckoutError) as caught:
        checkout_repository(repo_url='https://github.com/o/r.git', sha='a' * 40, dest=tmp_path / 'repo', token='raw-secret')
    assert 'raw-secret' not in str(caught.value)
    assert 'raw%2Dsecret' not in str(caught.value.details)
    assert 'PRIVATE_NAME' not in str(caught.value.details)
    assert caught.value.__cause__ is None
