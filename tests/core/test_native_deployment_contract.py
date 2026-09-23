from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core.errors import Range42Error
from tests.core.test_native_contexts import context
from tests.core.test_native_scenarios import scenario


def test_deployment_contract_retains_native_context_path_and_features():
    from app.schemas.v1.deployments import DeploymentCreate, AttemptCreate
    payload = DeploymentCreate(codename="TRAINING", scenario_label="exercise-a", project_id="p",
        target_host_id="h", team_count=1, project_sha="a" * 40,
        native={"context_id": "lab-demo", "path": "training/exercise-a", "features": {"WAZUH": True}})
    assert payload.model_dump()["native"]["features"] == {"WAZUH": True}
    assert AttemptCreate(scope="deploy_vms").scope == "deploy_vms"
    with pytest.raises(ValidationError):
        DeploymentCreate(codename="TRAINING", scenario_label="exercise-a", project_id="p",
            target_host_id="h", team_count=1, native={"context_id": "lab-demo", "path": "../outside"})


def test_native_replication_cannot_be_silently_ignored():
    from app.schemas.v1.deployments import DeploymentCreate
    with pytest.raises(ValidationError, match="team"):
        DeploymentCreate(codename="TRAINING", scenario_label="exercise-a", project_id="p",
            target_host_id="h", team_count=2, project_sha="a" * 40,
            native={"context_id": "lab-demo", "path": "training/exercise-a"})


@pytest.mark.asyncio
async def test_saved_native_deployment_uses_context_inventory_and_entire_checkout(tmp_path, monkeypatch):
    from app.core import scenario as module
    from app.core.models import Deployment, Project, Source, ProxmoxHost
    _, _, host = context(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    scenario(repo)
    project = SimpleNamespace(repo_owner="range42", repo_name="playbooks", source_id="source", subdir=None)
    source = SimpleNamespace(base_url="https://github.com", auth_kind="none", token_ref=None)
    class Session:
        async def get(self, model, identity):
            return {Project: project, Source: source, ProxmoxHost: host}[model]
    monkeypatch.setattr(module, "checkout_repository", lambda **kwargs: repo)
    deployment = Deployment(project_id="p", target_host_id="host", scenario_label="exercise-a", project_sha="a" * 40,
        native={"context_id": "lab-demo", "path": "training/exercise-a", "features": {"WAZUH": True}, "parameters": {}})
    prepared = await module.prepare_project_scenario(Session(), deployment, dest=tmp_path / "checkout")
    assert prepared.project_root == repo
    assert prepared.inventory == tmp_path / "contexts/lab-demo/inventory/inventory_default.yml"
    assert prepared.native["descriptor"]["actions"]["full"] == "exercise.setup.sh"
    assert prepared.vmids == [2001]
    with pytest.raises(Range42Error, match="action"):
        await module.prepare_project_scenario(Session(), deployment, dest=tmp_path / "other", scope="configure")


def test_native_scopes_do_not_expand_generated_deployment_permissions():
    from app.core.scenario import validate_concrete_scope
    generated = SimpleNamespace(scenario_label="lab", project_sha="a" * 40, native=None)
    with pytest.raises(Range42Error):
        validate_concrete_scope(generated, "delete_networks")
    native = SimpleNamespace(scenario_label="lab", project_sha="a" * 40,
        native={"descriptor": {"actions": {"full": "lab.setup.sh", "delete_networks": "lab.delete_networks.sh"}}})
    validate_concrete_scope(native, "delete_networks")
    with pytest.raises(Range42Error):
        validate_concrete_scope(native, "runtime")
