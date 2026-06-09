"""/v1/projects/{id}/compose + /validate.

compose: clones the pinned base catalog at base_catalog_sha, runs overlay
compose + optional expand_replication(team_count), returns the effective
document + sha256 doc hash.

validate: runs base and overlay through the generated Pydantic models and
the compose operator, returning structured field-level errors.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import ValidationError
from ruamel.yaml import YAML
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import git  # type: ignore

from app.core.db import get_session_factory
from app.core.errors import Range42Error, SourceUnreachableError
from app.core.models import Project
from app.overlay.compose import compose
from app.overlay.expand_replication import expand_replication
from app.schemas.generated import CatalogEntry, ProjectOverlay
from app.schemas.v1.projects import (
    ComposeRequest,
    ComposeResponse,
    ValidateResponse,
)

router = APIRouter()
_yaml = YAML(typ="safe")


async def _session() -> AsyncSession:
    async with get_session_factory()() as session:
        yield session


def _load_base_and_overlay(project: Project) -> tuple[dict, dict]:
    if not project.base_catalog_url or not project.base_catalog_sha:
        raise Range42Error(
            error="incomplete_project",
            code="PROJECT_NOT_PINNED",
            status=400,
            message="Project missing base_catalog_url/sha",
        )
    base_doc: dict | None = None
    with tempfile.TemporaryDirectory() as td:
        try:
            repo = git.Repo.clone_from(project.base_catalog_url, td)
            repo.git.checkout(project.base_catalog_sha)
        except Exception as e:
            raise SourceUnreachableError(
                details=[
                    {
                        "field": "base_catalog_url",
                        "reason": f"clone failed: {e}",
                    }
                ]
            )
        manifest = Path(td) / "range42.yaml"
        if manifest.exists():
            base_doc = _yaml.load(manifest.read_text()) or {}
    if base_doc is None:
        raise Range42Error(
            error="base_not_found",
            code="BASE_MANIFEST_MISSING",
            status=404,
            message="Base range42.yaml not found",
        )
    overlay_doc: dict = {}
    # Project's overlay.yaml lives in its own repo; v1 reads it from the local
    # workspace if present (absolute path only for safety).
    if project.subdir:
        overlay_path = Path(project.subdir) / "overlay.yaml"
        if overlay_path.is_absolute() and overlay_path.exists():
            overlay_doc = _yaml.load(overlay_path.read_text()) or {}
    return base_doc, overlay_doc


def _effective_hash(doc: dict) -> str:
    payload = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


@router.post("/{project_id}/compose", response_model=ComposeResponse)
async def compose_project(
    project_id: str,
    payload: ComposeRequest,
    session: AsyncSession = Depends(_session),
):
    proj = (
        await session.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if proj is None:
        raise Range42Error(
            error="not_found",
            code="NOT_FOUND",
            status=404,
            message=f"Project {project_id} not found",
        )
    base, overlay = _load_base_and_overlay(proj)
    eff = compose(base, overlay)
    if payload.team_count is not None:
        # expand_replication returns ExpandResult; the effective document
        # proper is under the "document" key (plan-inline fix).
        eff = expand_replication(eff, payload.team_count)["document"]
    return ComposeResponse(effective_doc=eff, effective_doc_hash=_effective_hash(eff))


@router.post("/{project_id}/validate", response_model=ValidateResponse)
async def validate_project(
    project_id: str, session: AsyncSession = Depends(_session)
):
    proj = (
        await session.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if proj is None:
        raise Range42Error(
            error="not_found",
            code="NOT_FOUND",
            status=404,
            message=f"Project {project_id} not found",
        )
    base, overlay = _load_base_and_overlay(proj)
    errors: list[dict] = []
    try:
        CatalogEntry.model_validate(base)
    except ValidationError as e:
        errors.extend(
            [
                {
                    "field": ".".join(str(x) for x in err["loc"]),
                    "reason": err["msg"],
                    "role": "base",
                }
                for err in e.errors()
            ]
        )
    if overlay:
        try:
            ProjectOverlay.model_validate(overlay)
        except ValidationError as e:
            errors.extend(
                [
                    {
                        "field": ".".join(str(x) for x in err["loc"]),
                        "reason": err["msg"],
                        "role": "overlay",
                    }
                    for err in e.errors()
                ]
            )
    try:
        compose(base, overlay)
    except Exception as e:
        errors.append(
            {"field": "compose", "reason": str(e), "role": "operator"}
        )
    return ValidateResponse(ok=not errors, errors=errors)
