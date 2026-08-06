"""Catalog-source git clones must authenticate with the Source's PAT and must
never leak that token in an error path."""
from pathlib import Path

import pytest

from app.core.errors import SourceUnreachableError
from app.core.models import Source, SourceRepo


def _src_repo():
    src = Source(id="s", provider="github", base_url="https://github.com",
                 auth_kind="pat", token_ref="ghp_SECRET")
    repo = SourceRepo(source_id="s", owner="o", repo="r", branch="main")
    return src, repo


def test_entries_clone_embeds_token(monkeypatch, tmp_path):
    from app.routes.v1.catalog import entries
    captured = {}

    class _FakeRepo:
        @staticmethod
        def clone_from(url, dest, **kw):
            captured["url"] = url
            Path(dest).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(entries.git, "Repo", _FakeRepo)
    src, repo = _src_repo()
    entries._clone_repo(src, repo, tmp_path)
    assert "x-access-token:ghp_SECRET@github.com" in captured["url"]


def test_entries_clone_error_is_redacted(monkeypatch, tmp_path):
    from app.routes.v1.catalog import entries

    class _BoomRepo:
        @staticmethod
        def clone_from(url, dest, **kw):
            raise RuntimeError(f"fatal: could not read from {url}")

    monkeypatch.setattr(entries.git, "Repo", _BoomRepo)
    src, repo = _src_repo()
    with pytest.raises(SourceUnreachableError) as ei:
        entries._clone_repo(src, repo, tmp_path)
    assert "ghp_SECRET" not in str(ei.value.details)


def test_refresh_clone_embeds_token_and_redacts(monkeypatch, tmp_path):
    import git as gitmod

    from app.routes.v1.catalog import refresh
    captured = {}

    class _FakeRepo:
        @staticmethod
        def clone_from(url, dest, **kw):
            captured["url"] = url
            Path(dest).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(gitmod, "Repo", _FakeRepo)
    src, repo = _src_repo()
    refresh._count_entries_in_repo(src, repo)
    assert "x-access-token:ghp_SECRET@github.com" in captured["url"]

    class _BoomRepo:
        @staticmethod
        def clone_from(url, dest, **kw):
            raise RuntimeError(f"fatal: {url}")

    monkeypatch.setattr(gitmod, "Repo", _BoomRepo)
    with pytest.raises(Exception) as ei:
        refresh._count_entries_in_repo(src, repo)
    assert "ghp_SECRET" not in str(ei.value)
