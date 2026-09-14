"""Resolve a pinned source bundle without blocking requests or leaking credentials."""

import asyncio
import json
import threading

import git
import pytest
from httpx import ASGITransport, AsyncClient

from tests.routes.test_catalog_sources import _boot
from tests.core.test_bundle_attachments import bundle as bundle, prepare


@pytest.mark.asyncio
async def test_resolve_route_fetches_the_requested_revision_and_returns_proof(
    bundle, tmp_path, monkeypatch
):
    tree = bundle["source"]
    repo = git.Repo.init(tree)
    repo.index.add(
        [
            str(path.relative_to(tree))
            for path in tree.rglob("*")
            if path.is_file() and ".git" not in path.parts
        ]
    )
    actor = git.Actor("Test", "test@example.invalid")
    sha = repo.index.commit("Selected content", author=actor, committer=actor).hexsha
    # A later commit differs; the request must still resolve the selected one.
    main = tree / bundle["path"] / "main.yml"
    main.write_text(main.read_text() + "# subsequent change\n")
    repo.index.add([str(main.relative_to(tree))])
    repo.index.commit("Later source revision", author=actor, committer=actor)
    app, dbmod = await _boot(tmp_path, monkeypatch)
    prepare(bundle)
    from app.core import project

    original = project._run_git
    seen = []

    def local_git(*args, **kwargs):
        args = list(args)
        if args[0] == "fetch":
            seen.append(args[-1])
            args[-2] = str(tree)
        return original(*args, **kwargs)

    monkeypatch.setattr(project, "_run_git", local_git)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as client:
            created = await client.post(
                "/v1/catalog/sources",
                json={
                    "provider": "github",
                    "base_url": "https://github.com",
                    "auth_kind": "none",
                    "repos": [{"owner": "range42", "repo": "range42-playbooks"}],
                },
            )
            source_id = created.json()["id"]
            response = await client.post(
                f"/v1/catalog/sources/{source_id}/bundles/resolve",
                json={"path": bundle["path"], "sha": sha, "target_kind": "VM"},
            )
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["source_sha"] == sha
            assert result["source_id"] == source_id
            assert result["runtime"]["proof"]
            assert seen == [sha]
            assert "root" not in json.dumps(result["runtime"]["dependencies"])
            invalid = await client.post(
                f"/v1/catalog/sources/{source_id}/bundles/resolve",
                json={"path": "../outside", "sha": "main", "target_kind": "VM"},
            )
            assert invalid.status_code == 422
    finally:
        await dbmod.dispose_engine()


@pytest.mark.asyncio
async def test_bundle_resolution_work_runs_off_the_event_loop(
    bundle, tmp_path, monkeypatch
):
    app, dbmod = await _boot(tmp_path, monkeypatch)
    from app.routes.v1.catalog import bundles

    entered, release = threading.Event(), threading.Event()

    def slow(*args):
        entered.set()
        release.wait(3)
        return {"resolved": True}

    monkeypatch.setattr(bundles, "_resolve_source_bundle", slow)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as client:
            created = await client.post(
                "/v1/catalog/sources",
                json={
                    "provider": "github",
                    "base_url": "https://github.com",
                    "auth_kind": "none",
                    "repos": [{"owner": "range42", "repo": "range42-playbooks"}],
                },
            )
            url = f"/v1/catalog/sources/{created.json()['id']}/bundles/resolve"
            pending = asyncio.create_task(
                client.post(
                    url,
                    json={"path": bundle["path"], "sha": "a" * 40, "target_kind": "VM"},
                )
            )
            assert await asyncio.to_thread(entered.wait, 1)
            health = await asyncio.wait_for(client.get("/v1/health"), 0.5)
            assert health.status_code == 200
            release.set()
            assert (await pending).status_code == 200
    finally:
        release.set()
        await dbmod.dispose_engine()
