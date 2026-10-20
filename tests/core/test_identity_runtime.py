"""The optional identity runtime cannot select commands or echo remote errors."""
import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def runtime():
    assert (ROOT / 'app/core/identity_runtime.py').exists(), 'Missing bounded identity transport'
    return importlib.import_module('app.core.identity_runtime')


@pytest.mark.asyncio
async def test_missing_or_writable_identity_configuration_is_unavailable(tmp_path, monkeypatch):
    module = runtime()
    from app.core.errors import Range42Error
    monkeypatch.delenv('RANGE42_IDENTITY_SSH_CONFIG', raising=False)
    with pytest.raises(Range42Error) as error:
        await module.identity_request({'version': 1, 'action': 'status', 'actor': 'staff-01'})
    assert error.value.status == 503
    config = tmp_path / 'ssh-config'
    config.write_text('invalid-secret-do-not-echo')
    config.chmod(0o666)
    monkeypatch.setenv('RANGE42_IDENTITY_SSH_CONFIG', str(config))
    with pytest.raises(Range42Error) as error:
        await module.identity_request({'version': 1, 'action': 'status', 'actor': 'staff-01'})
    assert error.value.status == 503 and 'invalid-secret' not in str(error.value)


def test_remote_protocol_refuses_unknown_codes_or_payloads_without_echo():
    module = runtime()
    from app.core.errors import Range42Error
    for raw, status, code in [
        (b'{"ok":false,"code":"IDENTITY_REVIEW_CONFLICT"}', 409, 'IDENTITY_REVIEW_CONFLICT'),
        (b'{"ok":false,"code":"IDENTITY_REQUEST_INVALID"}', 422, 'IDENTITY_REQUEST_INVALID'),
        (b'{"ok":false,"code":"secret-do-not-echo"}', 503, 'IDENTITY_RUNTIME_UNAVAILABLE'),
        (b'{"ok":true,"result":null}', 503, 'IDENTITY_RUNTIME_UNAVAILABLE'),
        (b'{"ok":true,"ok":false,"result":{}}', 503, 'IDENTITY_RUNTIME_UNAVAILABLE'),
    ]:
        with pytest.raises(Range42Error) as error:
            module.decode_result(raw)
        assert error.value.status == status and error.value.code == code
        assert 'secret-do-not-echo' not in str(error.value)
