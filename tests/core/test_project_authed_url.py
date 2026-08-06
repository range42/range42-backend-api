"""Unit tests for the shared authed_url helper used to embed a Git PAT into an
https clone URL (project + catalog clones reuse it)."""
from app.core.project import authed_url


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
