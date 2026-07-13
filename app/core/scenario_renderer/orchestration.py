"""Orchestration -- the playbooks that decide *when* each unit runs.

``main.yml`` is the scenario entrypoint the Ansible runner executes. Its one
load-bearing property is global stage discipline: EVERY tier's stage_00 (create
the VMs) runs before ANY tier's stage_01 (configure them), so a cross-tier
configure step -- a wazuh agent reaching the wazuh server in another tier -- always
finds its targets created and baselined. That is why main.yml imports each tier's
STAGE files rather than the tier's ``_main.yml`` wrapper (devkit-only, one tier at
a time). Imports inside a scenario are relative, which is what keeps the rendered
directory self-contained and portable.

A scenario that installs an XTIER bundle (the wazuh agent) ends on a *finalize*
step: the bundle acts on a group assembled across tiers, so it can only run once
every tier's stage_01 is done. main.yml therefore emits, after all stage_01
imports, a ``_build_<key>_active_group.yml`` (the runtime membership, gated by the
members' own INSTALL flags) immediately followed by a ``_finalize_<key>.yml``
(the bundle, pointed at that group). Both files live at the scenario root and
their names are a pure function of the group -- see ``finalize_*_filename`` -- so
the file writer and this orchestrator agree on them without further coordination.
"""

from __future__ import annotations

import yaml

from app.core.scenario_renderer._common import (
    ADD_HOST_RUNNER,
    PLAYBOOKS_ROOT,
    SCENARIOS_ROOT,
    gate_expr,
)
from app.core.scenario_renderer.registry import resolve_bundle_playbook
from app.core.scenario_renderer.types import (
    CrossTierGroup,
    ScenarioSpec,
    TierSpec,
    VmSpec,
)

_TEMPLATES_BOOTSTRAP = "./01_templates-bootstrap/_main.yml"

# add_host mutates the in-memory inventory rather than the host it runs on, but it
# still needs a play host: proxmox is the one group every scenario always has.

# Bundles and the scenario manifest are env-anchored (not relative): that is what
# lets a rendered scenario live in a project repo yet still find the playbooks repo.
# The env-anchoring here is confined to 01_templates-bootstrap/_main.yml, which
# imports bundles -- the top-level main.yml stays purely relative.

_CLOUDINIT_DOWNLOAD = (
    f"{PLAYBOOKS_ROOT}/bundles/core/proxmox/configure/templates/"
    "_main_download_cloudinit_files.yml"
)


def _dump(imports: list[str]) -> str:
    return yaml.safe_dump(
        [{"import_playbook": path} for path in imports],
        sort_keys=False,
        default_flow_style=False,
    )


def _dump_plays(plays: list[dict]) -> str:
    return yaml.safe_dump(plays, sort_keys=False, default_flow_style=False)


def _tier_dir(tier: TierSpec) -> str:
    """The tier's directory, relative to the scenario root."""
    return f"./{tier.number}_{tier.key}_infrastructure"


def _distinct_template_families(spec: ScenarioSpec) -> list[str]:
    """The template OS families, de-duplicated, first-seen order preserved.

    Several templates share one build bundle -- the bundle enumerates the vmids it
    must build from the manifest -- so one import per *distinct family* is enough.
    The family (not the hardware ``spec``) names the ``core/template.build.*`` bundle.
    """
    seen: list[str] = []
    for template in spec.templates:
        if template.os_family not in seen:
            seen.append(template.os_family)
    return seen


def render_templates_bootstrap(spec: ScenarioSpec) -> str:
    """Render ``01_templates-bootstrap/_main.yml`` -- the template provisioning step.

    Mirrors demo_lab's file: first download the shared cloud-init source files,
    then import each template-build bundle (``core/template.build.<spec>``, an
    INFRA bundle that runs on proxmox). The manifest path is handed down so the
    bundle can enumerate which template vmids to build (and skip the ones that
    already exist). main.yml imports this file only when the scenario declares
    templates; a scenario reusing another's templates has no bootstrap step.
    """
    plays: list[dict] = [{"import_playbook": _CLOUDINIT_DOWNLOAD}]
    manifest_path = f"{SCENARIOS_ROOT}/{spec.name}/manifest/scenario_vms.json"
    for family in _distinct_template_families(spec):
        bundle_main = resolve_bundle_playbook(f"core/template.build.{family}")
        plays.append(
            {
                "import_playbook": bundle_main,
                "vars": {
                    "manifest_path": manifest_path,
                    "template_bundle_dir": bundle_main[: -len("/main.yml")],
                },
            }
        )
    return _dump_plays(plays)


def render_main(spec: ScenarioSpec, *, include_templates: bool = True) -> str:
    """Render the scenario's ``main.yml`` (or ``main_vms_only.yml``).

    Order is the contract: templates-bootstrap (if any) -> every tier's stage_00
    -> every tier's stage_01 -> each cross-tier finalize (build its runtime group,
    then run its XTIER bundle against it). The finalize steps come dead last so the
    server they enrol against and every client VM already exist and are baselined.

    ``include_templates=False`` renders the ``main_vms_only.yml`` variant, which
    skips template creation to redeploy against templates that already exist.
    """
    imports: list[str] = []
    if spec.templates and include_templates:
        imports.append(_TEMPLATES_BOOTSTRAP)
    tiers = [t for t in spec.tiers if t.vms]  # a VM-less tier emits no stage files
    imports += [f"{_tier_dir(t)}/_main_stage_00.yml" for t in tiers]
    imports += [f"{_tier_dir(t)}/_main_stage_01.yml" for t in tiers]
    for group in spec.finalize:
        imports.append(f"./{finalize_group_build_filename(group)}")
        imports.append(f"./{finalize_import_filename(group)}")
    return _dump(imports)


def render_tier_stage00(tier: TierSpec) -> str:
    """Render a tier's ``_main_stage_00.yml`` -- its slice of the global create phase.

    Imports the tier's bootstrap group file only when the tier has VMs: an empty
    tier renders no ``_<group_id>.yml`` (``render_bootstrap_group`` returns ""),
    and importing a file that was never written is fatal at parse time.
    """
    imports: list[str] = []
    if tier.vms:
        imports.append(f"./stage_00-vm_bootstrap/_{tier.group_id}.yml")
    return _dump(imports)


def render_tier_stage01(tier: TierSpec) -> str:
    """Render a tier's ``_main_stage_01.yml`` -- its slice of the global configure phase.

    The active group is built first: it is an in-memory ``add_host`` group and the
    baseline (and later the finalize) target it, so a later build would leave them
    with no hosts. The baseline file is imported only when the tier has baseline
    bundles, and a per-VM configure file only for VMs carrying software bundles --
    the empty-content renderers write neither, and importing an unwritten file
    would fail at parse time.
    """
    imports = [f"./_build_{tier.key}_active_group.yml"]
    if tier.baseline_bundles:
        imports.append(f"./stage_01-vm_configure/_baseline_{tier.key}.yml")
    imports += [
        f"./stage_01-vm_configure/{vm.vm_name}.yml" for vm in tier.vms if vm.bundles
    ]
    return _dump(imports)


def _add_host_task(vm: VmSpec, group_id: str) -> dict:
    """One ``add_host`` task, gated by the member's own INSTALL flag when it has one.

    A member with no install flag is always enrolled; one whose VM is gated off at
    deploy time is skipped, so the XTIER bundle never hits a host that was never
    created. Mirror of ``baseline._add_host_task`` for the cross-tier group.
    """
    task: dict = {
        "name": f"add {vm.ssh_name} to {group_id}",
        "ansible.builtin.add_host": {"name": vm.ssh_name, "groups": group_id},
    }
    if vm.install_flag:
        task["name"] += f" when INSTALL_{vm.install_flag}=YES"
        task["when"] = gate_expr(vm.install_flag, vm.install_default)
    task["changed_when"] = False
    return task


def _finalize_key(group: CrossTierGroup) -> str:
    """The stem shared by a finalize group's two filenames, derived from its id.

    ``r42_demo_lab_wazuh_clients_active`` -> ``demo_lab_wazuh_clients``: strip the
    ``r42_`` inventory prefix and the ``_active`` runtime-group suffix. Pure
    function of the group so the file writer derives the same names.
    """
    key = group.group_id
    if key.startswith("r42_"):
        key = key[len("r42_") :]
    if key.endswith("_active"):
        key = key[: -len("_active")]
    return key


def finalize_group_build_filename(group: CrossTierGroup) -> str:
    """Root-level filename of the group's ``_build_<key>_active_group.yml``."""
    return f"_build_{_finalize_key(group)}_active_group.yml"


def finalize_import_filename(group: CrossTierGroup) -> str:
    """Root-level filename of the group's ``_finalize_<key>.yml``."""
    return f"_finalize_{_finalize_key(group)}.yml"


def render_finalize_group(group: CrossTierGroup) -> str:
    """Render ``_build_<key>_active_group.yml`` -- the cross-tier runtime membership.

    An ``add_host`` play that assembles ``group.group_id`` from ``group.members``,
    each member gated by its own INSTALL flag, so the group holds exactly the VMs
    that exist at deploy time. Mirror of demo_lab's
    ``_build_wazuh_clients_active_group.yml``.
    """
    play = {
        "name": f"build {group.group_id} dynamic group from INSTALL flags",
        "hosts": ADD_HOST_RUNNER,
        "gather_facts": False,
        "tasks": [_add_host_task(member, group.group_id) for member in group.members],
    }
    return _dump_plays([play])


def render_finalize(group: CrossTierGroup) -> str:
    """Render ``_finalize_<key>.yml`` -- the XTIER bundle run against the group.

    Imports ``group.bundle`` (resolved via the registry), gated by the bundle's
    own INSTALL flag, and passes ``group.group_var: group.group_id`` so the bundle
    finds its targets -- plus any vars the bundle carries. Mirror of demo_lab's
    ``_finalize-baseline-admin_wazuh_client.yml``.
    """
    block: dict = {"import_playbook": resolve_bundle_playbook(group.bundle.name)}
    if group.bundle.install_flag:
        block["when"] = gate_expr(
            group.bundle.install_flag, group.bundle.install_default
        )
    block["vars"] = {group.group_var: group.group_id, **group.bundle.vars}
    return _dump_plays([block])
