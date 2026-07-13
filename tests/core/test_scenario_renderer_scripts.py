"""Scenario renderer — the deployer-cli lifecycle scripts.

The generated scenario is deployed by the EXISTING deployer-cli (``range42-context``),
which we cannot change. It resolves the active scenario dir and execs
``<scenario>.setup.sh`` where ``<scenario>`` is the directory basename, so the script
filenames are a hard contract keyed on ``spec.name``. These tests pin the filenames,
the ``bash -n`` syntax cleanliness, and the exact idioms copied from the real
``demo_lab`` reference scripts (the ``:?`` vault guard, ``"$@"`` forwarding, the jq
mapfile + devkit loops).
"""

from __future__ import annotations

import subprocess

import pytest

from app.core.scenario_renderer.scripts import (
    render_delete_all_sh,
    render_delete_vms_only_sh,
    render_reset_setup_sh,
    render_scripts,
    render_setup_sh,
    render_setup_vms_only_sh,
)
from app.core.scenario_renderer.types import ScenarioSpec


def _spec(name: str = "demo_lab", **kw) -> ScenarioSpec:
    return ScenarioSpec(name=name, **kw)


def _bash_n(text: str, tmp_path) -> subprocess.CompletedProcess:
    script = tmp_path / "script.sh"
    script.write_text(text)
    return subprocess.run(
        ["bash", "-n", str(script)], capture_output=True, text=True
    )


ALL_RENDERERS = [
    render_setup_sh,
    render_setup_vms_only_sh,
    render_delete_all_sh,
    render_delete_vms_only_sh,
    render_reset_setup_sh,
]


class TestSyntaxAndShebang:
    @pytest.mark.parametrize("renderer", ALL_RENDERERS)
    def test_starts_with_shebang(self, renderer):
        assert renderer(_spec()).startswith("#!/bin/bash")

    @pytest.mark.parametrize("renderer", ALL_RENDERERS)
    def test_passes_bash_syntax_check(self, renderer, tmp_path):
        result = _bash_n(renderer(_spec()), tmp_path)
        assert result.returncode == 0, result.stderr


class TestSetup:
    def test_forwards_all_args(self):
        assert render_setup_sh(_spec()).rstrip().endswith('"$@"')

    def test_targets_main_playbook(self):
        assert '"./main.yml"' in render_setup_sh(_spec())

    def test_references_inventory_dir_var(self):
        assert "RANGE42_ANSIBLE_ROLES__INVENTORY_DIR" in render_setup_sh(_spec())

    def test_vault_var_uses_fail_loud_guard(self):
        # the :? idiom fails loudly if `range42-context use` was not run
        assert "${RANGE42_VAULT_PASSWORD_FILE:?" in render_setup_sh(_spec())


class TestSetupVmsOnly:
    def test_forwards_all_args(self):
        assert render_setup_vms_only_sh(_spec()).rstrip().endswith('"$@"')

    def test_targets_main_vms_only_playbook(self):
        text = render_setup_vms_only_sh(_spec())
        assert '"./main_vms_only.yml"' in text
        assert '"./main.yml"' not in text

    def test_vault_var_uses_fail_loud_guard(self):
        assert "${RANGE42_VAULT_PASSWORD_FILE:?" in render_setup_vms_only_sh(_spec())


class TestDeleteAll:
    def test_reads_both_vms_and_templates(self):
        text = render_delete_all_sh(_spec())
        assert "jq -r '.vms[].vm_id'" in text
        assert "jq -r '.templates[].vm_id'" in text

    def test_reads_manifest_from_relative_manifest_dir(self):
        assert "manifest/scenario_vms.json" in render_delete_all_sh(_spec())

    def test_calls_devkit_delete_binaries(self):
        text = render_delete_all_sh(_spec())
        assert "proxmox_vm.vm_id.stop_force.to.jsons.sh" in text
        assert "proxmox_vm.vm_id.delete.to.jsons.sh" in text
        assert "proxmox_vm.list.to.jsons.sh" in text

    def test_does_not_run_ansible_playbook(self):
        assert "ansible-playbook" not in render_delete_all_sh(_spec())


class TestDeleteVmsOnly:
    def test_reads_only_vms(self):
        text = render_delete_vms_only_sh(_spec())
        assert "jq -r '.vms[].vm_id'" in text
        assert ".templates[].vm_id" not in text

    def test_calls_devkit_delete_binaries(self):
        text = render_delete_vms_only_sh(_spec())
        assert "proxmox_vm.vm_id.stop_force.to.jsons.sh" in text
        assert "proxmox_vm.vm_id.delete.to.jsons.sh" in text


class TestResetSetup:
    def test_deletes_then_redeploys(self):
        text = render_reset_setup_sh(_spec())
        # delete phase reads only .vms (keep templates), then re-runs the deploy
        assert "jq -r '.vms[].vm_id'" in text
        assert ".templates[].vm_id" not in text
        assert "proxmox_vm.vm_id.delete.to.jsons.sh" in text
        assert "ansible-playbook" in text
        assert '"./main.yml"' in text

    def test_forwards_all_args(self):
        assert render_reset_setup_sh(_spec()).rstrip().endswith('"$@"')


class TestRenderScripts:
    def test_emits_exactly_the_five_mandatory_filenames_keyed_on_name(self):
        scripts = render_scripts(_spec(name="my_lab"))
        assert set(scripts) == {
            "my_lab.setup.sh",
            "my_lab.setup_vms_only.sh",
            "my_lab.delete_all.sh",
            "my_lab.delete_vms_only.sh",
            "my_lab.reset.setup.sh",
        }

    def test_values_match_the_individual_renderers(self):
        spec = _spec(name="forensics_lab")
        scripts = render_scripts(spec)
        assert scripts["forensics_lab.setup.sh"] == render_setup_sh(spec)
        assert scripts["forensics_lab.setup_vms_only.sh"] == render_setup_vms_only_sh(spec)
        assert scripts["forensics_lab.delete_all.sh"] == render_delete_all_sh(spec)
        assert scripts["forensics_lab.delete_vms_only.sh"] == render_delete_vms_only_sh(spec)
        assert scripts["forensics_lab.reset.setup.sh"] == render_reset_setup_sh(spec)

    def test_all_emitted_scripts_pass_bash_syntax_check(self, tmp_path):
        for filename, text in render_scripts(_spec(name="my_lab")).items():
            result = _bash_n(text, tmp_path)
            assert result.returncode == 0, f"{filename}: {result.stderr}"
