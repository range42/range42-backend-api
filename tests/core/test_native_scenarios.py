import json
from pathlib import Path

import pytest

from app.core.errors import Range42Error


def scenario(root: Path, name="training/exercise-a"):
    base = root / name
    (base / "manifest").mkdir(parents=True)
    (base / "templates").mkdir()
    (base / "main.yml").write_text("- hosts: all\n  tasks: []\n")
    (base / "manifest/scenario_vms.json").write_text(json.dumps({"version": 2, "vms": [{"vm_id": 2001}]}))
    (base / "templates/ansible-inventory.j2").write_text("all: {}\n")
    (base / "exercise.setup.sh").write_text('#!/bin/bash\nansible-playbook main.yml "$@"\n')
    (base / "exercise.delete_all.sh").write_text("#!/bin/bash\ntrue\n")
    (base / "exercise.reset.setup.sh").write_text("#!/bin/bash\ntrue\n")
    (base / "manifest/feature_flags.yml").write_text("features:\n- id: WAZUH\n  label: Monitoring\n  default: false\n")
    return base


def test_resolve_native_scenario_preserves_custom_root_and_script_names(tmp_path):
    from app.core.native_scenarios import inspect_native_scenario
    scenario(tmp_path)
    result = inspect_native_scenario(tmp_path, "training/exercise-a")
    assert result["path"] == "training/exercise-a"
    assert result["actions"]["full"] == "exercise.setup.sh"
    assert result["actions"]["teardown"] == "exercise.delete_all.sh"
    assert result["actions"]["reset"] == "exercise.reset.setup.sh"
    assert "configure" not in result["actions"]
    assert result["features"][0]["id"] == "WAZUH"


def test_native_parameters_bind_feature_flags_using_native_yes_no_convention(tmp_path):
    from app.core.native_scenarios import inspect_native_scenario, native_variables
    scenario(tmp_path)
    descriptor = inspect_native_scenario(tmp_path, "training/exercise-a")
    assert native_variables(descriptor, {"WAZUH": True}, {"sdn_test_vm_id": 60000}) == {
        "INSTALL_WAZUH": "YES", "sdn_test_vm_id": 60000,
    }
    assert native_variables(descriptor, {}, {}) == {"INSTALL_WAZUH": "NO"}
    with pytest.raises(Range42Error):
        native_variables(descriptor, {"UNKNOWN": True}, {})
    with pytest.raises(Range42Error):
        native_variables(descriptor, {}, {"INSTALL_WAZUH": "YES"})
    with pytest.raises(Range42Error):
        native_variables(descriptor, {}, {"ansible_host": "another-host"})


@pytest.mark.parametrize("name", ["../outside", "/absolute"])
def test_native_paths_cannot_escape_checkout(tmp_path, name):
    from app.core.native_scenarios import inspect_native_scenario
    with pytest.raises(Range42Error):
        inspect_native_scenario(tmp_path, name)


def test_native_script_symlinks_outside_checkout_are_rejected(tmp_path):
    from app.core.native_scenarios import inspect_native_scenario
    base = scenario(tmp_path / "repo")
    (base / "exercise.setup.sh").unlink()
    outside = tmp_path / "outside.sh"
    outside.write_text("true\n")
    (base / "exercise.setup.sh").symlink_to(outside)
    with pytest.raises(Range42Error):
        inspect_native_scenario(tmp_path / "repo", "training/exercise-a")


def test_native_plain_playbook_can_deploy_without_a_named_shell_wrapper(tmp_path):
    from app.core.native_scenarios import inspect_native_scenario
    base = scenario(tmp_path)
    (base / "exercise.setup.sh").unlink()
    result = inspect_native_scenario(tmp_path, "training/exercise-a")
    assert result["actions"]["full"] == "main.yml"


def test_native_network_diagnostic_is_not_reported_as_no_effect(tmp_path):
    from app.core.native_scenarios import inspect_native_scenario
    base = scenario(tmp_path)
    (base / "manifest/scenario_vms.json").write_text('{"version":2,"vms":[]}')
    result = inspect_native_scenario(tmp_path, "training/exercise-a")
    assert result["impact"] == "native_workflow"
    assert result["declared_vmids"] == []


def test_multiple_setup_scripts_require_explicit_entrypoint_instead_of_guessing(tmp_path):
    from app.core.native_scenarios import inspect_native_scenario
    base = scenario(tmp_path)
    (base / "another.setup.sh").write_text("true\n")
    with pytest.raises(Range42Error, match="ambiguous"):
        inspect_native_scenario(tmp_path, "training/exercise-a")
    (base / "manifest/scenario_runtime.json").write_text(json.dumps({
        "version": 1, "actions": {"full": "exercise.setup.sh"}, "bundle_path": "custom/bundles",
    }))
    (tmp_path / "custom/bundles").mkdir(parents=True)
    assert inspect_native_scenario(tmp_path, "training/exercise-a")["bundle_path"] == "custom/bundles"


@pytest.mark.parametrize("parameters", [{"api_token": "private"}, {"password": "private"},
    {"service_secret": "private"}, {"ratio": float("inf")}, {"ratio": float("nan")}])
def test_native_parameters_cannot_persist_credentials_or_nonfinite_values(tmp_path, parameters):
    from app.core.native_scenarios import inspect_native_scenario, native_variables
    scenario(tmp_path)
    with pytest.raises(Range42Error):
        native_variables(inspect_native_scenario(tmp_path, "training/exercise-a"), {}, parameters)


def test_diagnostic_defaults_and_parameter_overrides_are_included_in_vmid_checks(tmp_path):
    from app.core import native_scenarios
    from app.core.preflight import check_vmids
    base = scenario(tmp_path)
    (base / "manifest/scenario_vms.json").write_text('{"version":2,"vms":[]}')
    (base / "main.yml").write_text('- hosts: proxmox\n  vars:\n    global_template_vm_id: 9232\n  tasks:\n    - ansible.builtin.set_fact:\n        sdn_test_vm_id: 102\n')
    descriptor = native_scenarios.inspect_native_scenario(tmp_path, "training/exercise-a")
    assert descriptor["vmid_parameters"] == {"sdn_test_vm_id": [102]}
    assert check_vmids(native_scenarios.native_vmids(descriptor, {}), host_overrides=[[102, 102]]).result == "block"
    assert native_scenarios.native_vmids(descriptor, {"sdn_test_vm_id": 60000}) == [60000]
