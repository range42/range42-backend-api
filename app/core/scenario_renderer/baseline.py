"""The tier's runtime "active" group and the baseline that targets it.

stage_00 only creates a VM whose ``INSTALL_<FLAG>`` is YES, so a stage_01 play
bound to the static inventory group would go UNREACHABLE on every gated-off VM.
``render_active_group`` rebuilds the membership at deploy time with ``add_host``,
and ``render_baseline`` points the tier's bundles at that runtime group.
"""

from __future__ import annotations

import yaml

from app.core.scenario_renderer.registry import is_vm_level, resolve_bundle_playbook
from app.core.scenario_renderer.types import BundleRef, TierSpec, VmSpec

# add_host mutates the in-memory inventory rather than the host it runs on, but it
# still needs a play host: proxmox is the one group every scenario always has.
_ADD_HOST_RUNNER = "proxmox"


def _install_gate(install_flag: str, install_default: str) -> str:
    return f'INSTALL_{install_flag} | default("{install_default}") | upper == "YES"'


def _add_host_task(vm: VmSpec, active_group: str) -> dict:
    task: dict = {
        "name": f"add {vm.ssh_name} to {active_group}",
        "ansible.builtin.add_host": {"name": vm.ssh_name, "groups": active_group},
    }
    if vm.install_flag:
        task["name"] += f" when INSTALL_{vm.install_flag}=YES"
        task["when"] = _install_gate(vm.install_flag, vm.install_default)
    task["changed_when"] = False
    return task


def render_active_group(tier: TierSpec) -> str:
    """Render a tier's ``_build_<tier.key>_active_group.yml``."""
    play = {
        "name": f"build {tier.active_group} dynamic group from INSTALL flags",
        "hosts": _ADD_HOST_RUNNER,
        "gather_facts": False,
        "tasks": [_add_host_task(vm, tier.active_group) for vm in tier.vms],
    }
    return yaml.safe_dump([play], sort_keys=False, default_flow_style=False)


def _baseline_block(bundle: BundleRef, active_group: str) -> dict:
    if is_vm_level(bundle.name):
        raise ValueError(
            f"bundle {bundle.name!r} is VM-level and cannot be a tier baseline: a "
            "baseline runs against target_group, but this bundle reads "
            "global_vm_ssh_name and would silently target nothing at deploy time"
        )
    block: dict = {"import_playbook": resolve_bundle_playbook(bundle.name)}
    if bundle.install_flag:
        block["when"] = _install_gate(bundle.install_flag, bundle.install_default)
    block["vars"] = {"target_group": active_group, **bundle.vars}
    return block


def render_baseline(tier: TierSpec) -> str:
    """Render a tier's ``stage_01-vm_configure/_baseline_<tier.key>.yml``.

    Returns ``""`` for a tier with no baseline bundles -- ``yaml.safe_dump([])``
    is ``"[]\\n"``, which Ansible refuses to import ("a playbook must contain at
    least one play"). The empty string is the caller's falsy signal to write no
    file and emit no ``import_playbook`` for this baseline.

    Raises ``ValueError`` if a VM-level bundle is placed in the tier baseline.
    """
    if not tier.baseline_bundles:
        return ""
    blocks = [_baseline_block(b, tier.active_group) for b in tier.baseline_bundles]
    return yaml.safe_dump(blocks, sort_keys=False, default_flow_style=False)
