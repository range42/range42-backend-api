import pytest
from pydantic import ValidationError

from app.core.errors import Range42Error
from app.core.models import Deployment
from app.core.scenario import validate_concrete_scope
from app.schemas.v1.deployments import DeploymentCreate


def test_new_deployments_require_a_concrete_scenario_name():
    with pytest.raises(ValidationError, match="retired"):
        DeploymentCreate(codename="LAB", scenario_label="_universal", project_id="p", target_host_id="h", team_count=1)


@pytest.mark.parametrize("scope", ["full", "configure", "teardown", "runtime"])
def test_existing_universal_deployment_cannot_execute(scope):
    dep = Deployment(scenario_label="_universal", project_sha="a" * 40)
    with pytest.raises(Range42Error) as exc:
        validate_concrete_scope(dep, scope)
    assert exc.value.code == "SCENARIO_RETIRED"


def test_internal_runtime_scope_can_address_a_pinned_concrete_deployment():
    validate_concrete_scope(Deployment(scenario_label="concrete", project_sha="a" * 40), "runtime")
