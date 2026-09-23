"""Native context discovery and read-only saved-scenario previews."""
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session_factory
from app.core.models import ProxmoxHost
from app.core.native_contexts import available_contexts
from app.core.native_scenarios import inside, inspect_native_scenario
from app.core.scenario import checkout_project_repository

router = APIRouter(tags=["v1 native scenarios"])


async def _session():
    async with get_session_factory()() as session:
        yield session


@router.get("/contexts")
async def contexts(session: AsyncSession = Depends(_session)):
    hosts = list((await session.execute(select(ProxmoxHost))).scalars())
    rows = await asyncio.to_thread(available_contexts, hosts)
    return {"items": rows, "total": len(rows), "setup_hint": (
        "Initialize an environment with range42-context init on the deployer-cli. "
        "Run this backend there as the deployment user and configure RANGE42_CONTEXT_ROOT "
        "and RANGE42_CONTEXT_SCRIPT, or register existing workspaces with RANGE42_NATIVE_CONTEXTS_FILE.")}


@router.get("/projects/{project_id}/native-scenario")
async def preview(project_id: str, path: str = Query(min_length=1, max_length=1024),
                  sha: str = Query(pattern=r"^(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})$"),
                  session: AsyncSession = Depends(_session)):
    with TemporaryDirectory(prefix="r42-native-preview-") as directory:
        root, _, project = await checkout_project_repository(session, project_id,
            dest=Path(directory) / "checkout", sha=sha)
        if project.subdir:
            root = inside(root, project.subdir)
        return await asyncio.to_thread(inspect_native_scenario, root, path)
