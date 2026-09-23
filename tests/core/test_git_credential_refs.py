"""External Git secrets are opt-in, bounded and never returned in errors."""
import os

import pytest

from app.core import credential_store
from app.core.errors import Range42Error


def resolve(value):
    return credential_store.resolve_git_credential(value)


@pytest.fixture
def secret_dir(tmp_path, monkeypatch):
    root = tmp_path / 'approved'
    root.mkdir(mode=0o700)
    monkeypatch.setenv('RANGE42_GIT_SECRET_DIR', str(root))
    monkeypatch.setenv('RANGE42_GIT_SECRET_ENV_ALLOWLIST', 'R42_TEST_PAT')
    return root


def test_literal_and_anonymous_credentials_remain_compatible():
    assert resolve(None) is None
    assert resolve('literal-pat') == 'literal-pat'


def test_env_reference_rotation_is_observed_without_changing_reference(secret_dir, monkeypatch):
    monkeypatch.setenv('R42_TEST_PAT', 'first-secret')
    assert resolve('env://R42_TEST_PAT') == 'first-secret'
    monkeypatch.setenv('R42_TEST_PAT', 'second-secret')
    assert resolve('env://R42_TEST_PAT') == 'second-secret'


@pytest.mark.parametrize('value,code', [
    ('env://UNAPPROVED_PRIVATE_NAME', 'GIT_CREDENTIAL_REFERENCE_DENIED'),
    ('env://R42_TEST_PAT', 'GIT_CREDENTIAL_REFERENCE_UNAVAILABLE'),
    ('env://R42_TEST_PAT/extra', 'GIT_CREDENTIAL_REFERENCE_INVALID'),
    ('file:///etc/PRIVATE_PATH', 'GIT_CREDENTIAL_REFERENCE_DENIED'),
])
def test_missing_denied_and_malformed_references_have_fixed_safe_errors(secret_dir, monkeypatch, value, code):
    monkeypatch.delenv('R42_TEST_PAT', raising=False)
    with pytest.raises(Range42Error) as caught:
        resolve(value)
    error = caught.value
    assert error.code == code
    assert error.status == 503
    assert value not in str(error)
    assert 'PRIVATE' not in str(error.details)
    assert error.__cause__ is None


def test_references_are_disabled_without_operator_configuration(monkeypatch):
    monkeypatch.delenv('RANGE42_GIT_SECRET_DIR', raising=False)
    monkeypatch.delenv('RANGE42_GIT_SECRET_ENV_ALLOWLIST', raising=False)
    for value in ('env://R42_TEST_PAT', 'file:///anywhere'):
        with pytest.raises(Range42Error) as caught:
            resolve(value)
        assert caught.value.code == 'GIT_CREDENTIAL_REFERENCE_DENIED'


def test_file_rotation_reads_new_bytes_and_accepts_one_terminal_newline(secret_dir):
    path = secret_dir / 'token'
    path.write_bytes(b'first-secret\r\n')
    path.chmod(0o600)
    assert resolve(path.as_uri()) == 'first-secret'
    replacement = secret_dir / 'next'
    replacement.write_text('second-secret\n')
    replacement.chmod(0o400)
    replacement.replace(path)
    assert resolve(path.as_uri()) == 'second-secret'


@pytest.mark.parametrize('shape', ['missing', 'directory', 'symlink', 'parent_symlink', 'hardlink', 'public', 'fifo', 'outside', 'traversal'])
def test_file_reference_refuses_unsupported_or_unowned_layout(secret_dir, tmp_path, shape):
    path = secret_dir / 'token'
    outside = tmp_path / 'external'
    outside.write_text('must-not-be-read')
    outside.chmod(0o600)
    if shape == 'directory':
        path.mkdir()
    elif shape == 'symlink':
        path.symlink_to(outside)
    elif shape == 'parent_symlink':
        (secret_dir / 'link').symlink_to(tmp_path, target_is_directory=True)
        path = secret_dir / 'link/external'
    elif shape == 'hardlink':
        os.link(outside, path)
    elif shape == 'public':
        path.write_text('public-secret')
        path.chmod(0o644)
    elif shape == 'fifo':
        os.mkfifo(path, 0o600)
    elif shape == 'outside':
        path = outside
    elif shape == 'traversal':
        path = secret_dir / '../external'
    with pytest.raises(Range42Error) as caught:
        resolve(path.as_uri())
    assert caught.value.code.startswith('GIT_CREDENTIAL_REFERENCE_')
    assert str(path) not in str(caught.value)


@pytest.mark.parametrize('contents', [b'', b'\n', b'abc\nsecret', b'abc\0secret', b' x ', b'secret\r', b'\xff', b'x' * 8193], ids=['empty', 'newline', 'multiline', 'nul', 'whitespace', 'bare-cr', 'utf8', 'oversize'])
def test_file_values_fail_closed_on_empty_multiline_invalid_or_oversized_content(secret_dir, contents):
    path = secret_dir / 'token'
    path.write_bytes(contents)
    path.chmod(0o600)
    with pytest.raises(Range42Error) as caught:
        resolve(path.as_uri())
    assert caught.value.code == 'GIT_CREDENTIAL_REFERENCE_INVALID'
    assert caught.value.__cause__ is None


def test_reference_is_not_recursively_resolved(secret_dir, monkeypatch):
    monkeypatch.setenv('R42_TEST_PAT', 'env://OTHER')
    assert resolve('env://R42_TEST_PAT') == 'env://OTHER'


@pytest.mark.parametrize('rotation', ['replace', 'rewrite'])
def test_concurrent_file_rotation_refuses_mixed_or_disappearing_identity(secret_dir, monkeypatch, rotation):
    path = secret_dir / 'token'
    path.write_text('first-secret')
    path.chmod(0o600)
    original = os.read

    def read_then_rotate(descriptor, size):
        value = original(descriptor, size)
        if rotation == 'replace':
            replacement = secret_dir / 'next'
            replacement.write_text('other-secret')
            replacement.chmod(0o600)
            replacement.replace(path)
        else:
            path.write_text('other-secret')
        return value

    monkeypatch.setattr(os, 'read', read_then_rotate)
    with pytest.raises(Range42Error) as caught:
        resolve(path.as_uri())
    assert caught.value.code == 'GIT_CREDENTIAL_REFERENCE_CHANGED'


def test_operation_snapshot_hides_secrets_and_never_modifies_the_orm_value(secret_dir, monkeypatch):
    from app.core.models import Source
    monkeypatch.setenv('R42_TEST_PAT', 'resolved-private-token')
    source = Source(id='source', provider='github', base_url='https://github.com', auth_kind='pat', token_ref='env://R42_TEST_PAT')
    snapshot = credential_store.resolved_git_source(source)
    monkeypatch.setenv('R42_TEST_PAT', 'different-private-token')
    assert credential_store.resolved_git_source(snapshot) is snapshot
    assert snapshot.token_ref == 'resolved-private-token'
    assert source.token_ref == 'env://R42_TEST_PAT'
    assert 'resolved-private-token' not in repr(snapshot)
    assert 'R42_TEST_PAT' not in repr(snapshot)


def test_public_source_ignores_registered_reference_without_reading_it(monkeypatch):
    from app.core.models import Source
    monkeypatch.delenv('RANGE42_GIT_SECRET_ENV_ALLOWLIST', raising=False)
    source = Source(id='source', provider='github', base_url='https://github.com', auth_kind='none', token_ref='env://UNAVAILABLE')
    assert credential_store.resolved_git_source(source).token_ref is None
