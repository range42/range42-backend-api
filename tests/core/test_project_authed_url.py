"""Unit tests for the shared authed_url helper used to embed a Git PAT into an
https clone URL (project + catalog clones reuse it)."""
from urllib.parse import unquote, urlsplit

from app.core.project import _redact_authed_url, authed_url


def test_embeds_token_as_x_access_token():
    assert authed_url("https://github.com/o/r.git", "ghp_SECRET") == (
        "https://x-access-token:ghp_SECRET@github.com/o/r.git"
    )


def test_none_token_leaves_url_unchanged():
    assert authed_url("https://github.com/o/r.git", None) == (
        "https://github.com/o/r.git"
    )


def test_empty_token_leaves_url_unchanged():
    assert authed_url("https://github.com/o/r.git", "") == (
        "https://github.com/o/r.git"
    )


def test_non_https_url_is_untouched():
    assert authed_url("git@github.com:o/r.git", "ghp_SECRET") == (
        "git@github.com:o/r.git"
    )


def test_token_reserved_characters_are_encoded_as_url_userinfo():
    token = "prefix@SECRET_SUFFIX:p/a?ss#%"
    result = authed_url("https://example.test/o/r.git", token)
    parsed = urlsplit(result)
    assert parsed.hostname == "example.test"
    assert parsed.path == "/o/r.git"
    assert unquote(parsed.password) == token
    assert "prefix%40SECRET_SUFFIX%3Ap%2Fa%3Fss%23%25" in result


def test_redaction_removes_entire_token_containing_at_sign():
    url = authed_url("https://example.test/o/r.git", "prefix@SECRET_SUFFIX")
    assert _redact_authed_url(f"fetch {url} failed") == (
        "fetch https://[REDACTED]@example.test/o/r.git failed"
    )
