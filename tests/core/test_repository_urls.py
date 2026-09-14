"""Git ingress is restricted to operator-approved server authorities."""
import pytest
from pydantic import ValidationError

from app.core.errors import Range42Error
from app.core.models import Project
from app.schemas.v1.catalog import SourceIn
from app.schemas.v1.projects import ProjectIn


@pytest.mark.parametrize("url", ["https://127.0.0.1/repo.git", "https://169.254.169.254/repo.git", "https://attacker.example/repo.git", "file:///etc/passwd", "ext::sh -c anything", "https://github.com@attacker.example/r.git"])
def test_source_rejects_unapproved_repository_host(url, monkeypatch):
    monkeypatch.delenv("RANGE42_GIT_ALLOWED_HOSTS", raising=False)
    with pytest.raises(ValidationError):
        SourceIn(provider="generic", base_url=url, auth_kind="none")


def test_private_git_server_requires_explicit_operator_allowlist(monkeypatch):
    monkeypatch.setenv("RANGE42_GIT_ALLOWED_HOSTS", "github.com,git.internal:8443")
    source = SourceIn(provider="gitea", base_url="https://git.internal:8443/forge", auth_kind="none")
    assert str(source.base_url) == "https://git.internal:8443/forge"
    with pytest.raises(ValidationError):
        SourceIn(provider="gitea", base_url="https://git.internal:9999", auth_kind="none")


def test_project_base_catalog_url_uses_the_same_policy():
    with pytest.raises(ValidationError):
        ProjectIn(name="test", source_id="s", branch_strategy="dedicated_repo", base_catalog_url="file:///tmp/internal")


def test_compose_blocks_legacy_database_urls_before_cloning(monkeypatch):
    from app.routes.v1.projects import compose
    called = []
    monkeypatch.setattr(compose.git.Repo, "clone_from", lambda *a, **kw: called.append(a))
    project = Project(id="p", base_catalog_url="http://169.254.169.254/latest/meta-data", base_catalog_sha="a" * 40)
    with pytest.raises(Range42Error) as exc:
        compose._load_base_and_overlay(project)
    assert exc.value.code == "GIT_URL_REJECTED"
    assert called == []


@pytest.mark.parametrize("operation", ["catalog", "project"])
def test_git_does_not_follow_http_redirects(tmp_path, monkeypatch, operation):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from app.core.models import Source, SourceRepo
    from app.routes.v1.catalog.entries import _clone_repo

    requests = []
    class RedirectHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(302 if not self.path.startswith("/metadata") else 403)
            self.send_header("Location", "/metadata")
            self.end_headers()
        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    authority = f"127.0.0.1:{server.server_port}"
    monkeypatch.setenv("RANGE42_GIT_ALLOWED_HOSTS", authority)
    monkeypatch.setenv("RANGE42_GIT_ALLOW_HTTP", "1")
    try:
        source = Source(id="s", provider="gitea", base_url=f"http://{authority}", auth_kind="none")
        repository = SourceRepo(id="r", source_id="s", owner="o", repo="r", branch="main")
        with pytest.raises(Range42Error):
            if operation == "catalog":
                _clone_repo(source, repository, tmp_path)
            else:
                from app.core.project import checkout_repository
                checkout_repository(repo_url=f"http://{authority}/o/r.git", sha="a" * 40, dest=tmp_path / "checkout", token=None)
        assert requests
        assert all(not path.startswith("/metadata") for path in requests)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
