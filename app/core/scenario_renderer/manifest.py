"""manifest/scenario_vms.json -- the scenario's identity declaration (schema v2).

Consumed by real tooling: the installer auto-discovers scenarios by this file's
presence, the deployer CLI drives start/stop/snapshot/teardown off ``.vms[].vm_id``
and ``.vms[].ip``, and the repo-wide ``_reserved.json`` ledger is regenerated from
every scenario's copy to catch VMID/IP collisions.
"""

from __future__ import annotations

import json

from app.core.scenario_renderer.types import ScenarioSpec, TemplateSpec, VmSpec


def _vm_entry(vm: VmSpec) -> dict:
    return {
        "vm_id": vm.vm_id,
        "vm_name": vm.vm_name,
        "ip": vm.ip,
        "role": vm.role,
        "bridge": vm.bridge,
    }


def _template_entry(template: TemplateSpec) -> dict:
    return {
        "vm_id": template.vm_id,
        "vm_name": template.vm_name,
        "spec": template.spec,
        "ip": template.ip,
        "bridge": template.bridge,
    }


def _duplicates(values: list) -> list:
    """The values appearing more than once, first-seen order preserved."""
    seen: set = set()
    dupes: dict = {}  # dict, not set: keeps order and de-dupes the report
    for value in values:
        if value in seen:
            dupes[value] = None
        seen.add(value)
    return list(dupes)


def _validate(vms: list[VmSpec], templates: list[TemplateSpec], scenario: str) -> None:
    """Enforce, at author time, the collisions ``_check_reserved.sh`` catches at commit.

    Only the intra-scenario half is knowable here; the cross-scenario half stays the
    ledger's job. Raising early keeps a UI-authored scenario from poisoning it.
    """
    dupe_ids = _duplicates([vm.vm_id for vm in vms])
    if dupe_ids:
        raise ValueError(f"{scenario}: duplicate vm_id between VMs: {sorted(dupe_ids)}")

    dupe_addrs = _duplicates([(vm.bridge, vm.ip) for vm in vms])
    if dupe_addrs:
        pairs = ", ".join(f"{bridge}/{ip}" for bridge, ip in sorted(dupe_addrs))
        raise ValueError(f"{scenario}: duplicate (bridge, ip) between VMs: {pairs}")

    crossed = sorted(
        {vm.vm_id for vm in vms} & {template.vm_id for template in templates}
    )
    if crossed:
        raise ValueError(
            f"{scenario}: vm_id collision between a VM and a template: {crossed}"
        )


def render_scenario_vms(spec: ScenarioSpec) -> str:
    """Render ``manifest/scenario_vms.json`` for ``spec``.

    Every VM of every tier is declared, including ones whose ``install_flag``
    defaults to NO: the manifest reserves the vmid/IP slot, it does not describe
    what this particular deploy creates.

    Raises:
        ValueError: on a duplicate ``vm_id``, a duplicate ``(bridge, ip)``, or a
            ``vm_id`` shared by a VM and a template.
    """
    _validate(list(spec.all_vms), list(spec.templates), spec.name)

    vms = sorted(spec.all_vms, key=lambda vm: vm.vm_id)
    templates = sorted(spec.templates, key=lambda template: template.vm_id)
    doc = {
        "scenario": spec.name,
        "version": 2,
        "description": spec.description,
        "vms": [_vm_entry(vm) for vm in vms],
        "templates": [_template_entry(template) for template in templates],
    }
    return json.dumps(doc, indent=2) + "\n"
