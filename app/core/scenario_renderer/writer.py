"""Assemble every renderer into a complete scenario directory.

``render_scenario`` returns a ``{relative_path: content}`` map that satisfies the
deployer-cli's mandatory contract: the four ``templates/*.j2`` discovery-gate files,
``manifest/scenario_vms.json`` + ``feature_flags.yml``, ``main.yml`` and the per-tier
stage files it imports, and the five lifecycle scripts named for the scenario. It
never emits ``secrets/`` -- the installer symlinks that in and deletes a real dir.

A file whose renderer returns ``""`` (an empty tier, a bundle-less VM) is simply not
placed, and the orchestration layer already refrains from importing it.
"""

from __future__ import annotations

from app.core.scenario_renderer.baseline import render_active_group, render_baseline
from app.core.scenario_renderer.bootstrap import render_bootstrap_group
from app.core.scenario_renderer.flags import render_feature_flags
from app.core.scenario_renderer.manifest import render_scenario_vms
from app.core.scenario_renderer.orchestration import (
    finalize_group_build_filename,
    finalize_import_filename,
    render_finalize,
    render_finalize_group,
    render_main,
    render_templates_bootstrap,
    render_tier_stage00,
    render_tier_stage01,
)
from app.core.scenario_renderer.scripts import render_scripts
from app.core.scenario_renderer.stage01 import render_vm_software
from app.core.scenario_renderer.types import ScenarioSpec, TierSpec
from app.core.scenario_renderer.workspace_templates import (
    render_ansible_vars,
    render_inventory_template,
    render_ssh_config_template,
    render_vault_example,
)


def _tier_dir(tier: TierSpec) -> str:
    return f"{tier.number}_{tier.key}_infrastructure"


def _put(files: dict[str, str], path: str, content: str) -> None:
    """Place a file only when its renderer produced content.

    Empty content is the agreed signal for 'nothing to render here'; the
    orchestration layer never imports a file that was skipped this way.
    """
    if content:
        files[path] = content


def _add_tier(files: dict[str, str], tier: TierSpec) -> None:
    d = _tier_dir(tier)
    files[f"{d}/_main_stage_00.yml"] = render_tier_stage00(tier)
    files[f"{d}/_main_stage_01.yml"] = render_tier_stage01(tier)
    files[f"{d}/_build_{tier.key}_active_group.yml"] = render_active_group(tier)

    _put(files, f"{d}/stage_00-vm_bootstrap/_{tier.group_id}.yml",
         render_bootstrap_group(list(tier.vms), tier.group_id))
    _put(files, f"{d}/stage_01-vm_configure/_baseline_{tier.key}.yml", render_baseline(tier))
    for vm in tier.vms:
        _put(files, f"{d}/stage_01-vm_configure/{vm.vm_name}.yml", render_vm_software(vm))


def render_scenario(spec: ScenarioSpec) -> dict[str, str]:
    """Render the full scenario directory as a ``{path: content}`` map."""
    files: dict[str, str] = {}

    files["main.yml"] = render_main(spec)
    files["main_vms_only.yml"] = render_main(spec, include_templates=False)

    if spec.templates:
        files["01_templates-bootstrap/_main.yml"] = render_templates_bootstrap(spec)

    for tier in spec.tiers:
        _add_tier(files, tier)

    for group in spec.finalize:
        files[finalize_group_build_filename(group)] = render_finalize_group(group)
        files[finalize_import_filename(group)] = render_finalize(group)

    files["manifest/scenario_vms.json"] = render_scenario_vms(spec)
    files["manifest/feature_flags.yml"] = render_feature_flags(spec)

    files["templates/ansible-inventory.j2"] = render_inventory_template(spec)
    files["templates/ssh-config.j2"] = render_ssh_config_template(spec)
    files["templates/ansible-vars.yml"] = render_ansible_vars(spec)
    files["templates/vault-example.yml"] = render_vault_example(spec)

    files.update(render_scripts(spec))

    return files
