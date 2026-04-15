"""Shape tests for /v1 DTOs — enough to catch regressions in validators."""
import pytest
from pydantic import ValidationError

from app.schemas.v1.catalog import SourceIn
from app.schemas.v1.common import Page
from app.schemas.v1.deployments import (
    AttemptCreate,
    DeploymentCreate,
    RollbackRequest,
    SnapshotCreate,
)
from app.schemas.v1.projects import ProjectIn
from app.schemas.v1.proxmox import HostIn


def test_deployment_create_enforces_codename():
    with pytest.raises(ValidationError):
        DeploymentCreate(
            codename="too-lowercase",
            scenario_label="x",
            project_id="p",
            target_host_id="h",
            team_count=1,
        )


def test_deployment_create_happy():
    m = DeploymentCreate(
        codename="ALPHA",
        scenario_label="sc",
        project_id="p",
        target_host_id="h",
        team_count=3,
    )
    assert m.team_count == 3


def test_attempt_scope_pattern():
    AttemptCreate(scope="full")
    with pytest.raises(ValidationError):
        AttemptCreate(scope="nonsense")


def test_snapshot_scope_pattern():
    SnapshotCreate(scope="team", team_id=1)
    with pytest.raises(ValidationError):
        SnapshotCreate(scope="invalid")


def test_rollback_scope_pattern():
    RollbackRequest(scope="all")
    with pytest.raises(ValidationError):
        RollbackRequest(scope="bogus")


def test_source_in_provider_pattern():
    SourceIn(provider="github", base_url="https://github.com", auth_kind="none")
    with pytest.raises(ValidationError):
        SourceIn(provider="bitbucket", base_url="https://x", auth_kind="none")


def test_source_in_auth_kind_pattern():
    with pytest.raises(ValidationError):
        SourceIn(provider="github", base_url="https://github.com", auth_kind="token")


def test_project_in_branch_strategy_pattern():
    ProjectIn(name="p", source_id="s", branch_strategy="shared_repo_subdir")
    ProjectIn(name="p", source_id="s", branch_strategy="dedicated_repo")
    with pytest.raises(ValidationError):
        ProjectIn(name="p", source_id="s", branch_strategy="bogus")


def test_host_in_defaults():
    h = HostIn(
        name="pve01",
        api_url="https://pve01:8006",
        node_name="pve01",
        token_ref="tok",
    )
    assert h.default_bridge == "vmbr0"


def test_page_is_generic():
    p: Page[int] = Page[int](items=[1, 2, 3], total=3)
    assert p.offset == 0 and p.limit == 100 and p.total == 3
