"""manifest/feature_flags.yml -- the scenario's deploy-time toggle contract.

Every ``INSTALL_<ID>`` gate the scenario declares -- on a VM (is it created at
all?) or on a bundle (is it installed?) -- surfaces here once, with the default
the rendered playbooks fall back to. The deploy TUI renders this list as its
checkbox modal, so a missing, duplicated or desynced entry is operator-visible.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

import yaml

from app.core.scenario_renderer.types import BundleRef, ScenarioSpec, VmSpec


class _IndentedDumper(yaml.SafeDumper):
    """Indent sequences under their key, matching the hand-authored scenario files."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        super().increase_indent(flow, False)


@dataclass(frozen=True)
class _Decl:
    """One site where a flag is declared -- a VM gate or a bundle gate."""

    flag: str
    default: bool
    label: str  # authored copy, "" when the site declares none
    description: str
    source: str  # vm_name or bundle name, for the derived fallback copy
    from_bundle: bool


def _is_yes(install_default: str) -> bool:
    return install_default.strip().upper() == "YES"


def _vm_decl(vm: VmSpec) -> _Decl:
    return _Decl(
        flag=vm.install_flag or "",
        default=_is_yes(vm.install_default),
        label="",
        description=vm.description,
        source=vm.vm_name,
        from_bundle=False,
    )


def _bundle_decl(bundle: BundleRef) -> _Decl:
    return _Decl(
        flag=bundle.install_flag or "",
        default=_is_yes(bundle.install_default),
        label=bundle.label,
        description=bundle.description,
        source=bundle.name,
        from_bundle=True,
    )


def _declarations(spec: ScenarioSpec) -> list[_Decl]:
    decls: list[_Decl] = []
    for tier in spec.tiers:
        for bundle in tier.baseline_bundles:
            if bundle.install_flag:
                decls.append(_bundle_decl(bundle))
        for vm in tier.vms:
            if vm.install_flag:
                decls.append(_vm_decl(vm))
            for bundle in vm.bundles:
                if bundle.install_flag:
                    decls.append(_bundle_decl(bundle))
    return decls


def _group(decls: Iterable[_Decl]) -> dict[str, list[_Decl]]:
    grouped: dict[str, list[_Decl]] = defaultdict(list)
    for decl in decls:
        grouped[decl.flag].append(decl)
    return grouped


def _first(*candidates: str) -> str:
    return next((c for c in candidates if c), "")


def _vm_fallback(vms: list[_Decl]) -> tuple[str, str]:
    if not vms:
        return "", ""
    names = ", ".join(vm.source for vm in vms)
    noun = "VM" if len(vms) == 1 else "VMs"
    return f"Deploy {names}", f"Creates the {names} {noun}."


def _bundle_fallback(bundles: list[_Decl]) -> tuple[str, str]:
    if not bundles:
        return "", ""
    name = bundles[0].source
    return f"Install {name}", f"Runs the {name} bundle."


def _default_of(flag: str, decls: list[_Decl]) -> bool:
    defaults = {decl.default for decl in decls}
    if len(defaults) > 1:
        raise ValueError(
            f'conflicting install_default for flag {flag}: declared both "YES" and "NO"'
        )
    return defaults.pop()


def _feature(flag: str, decls: list[_Decl]) -> dict:
    """Collapse every declaration of a flag into the single entry the TUI shows.

    Authored bundle copy wins (it describes the feature), then the VM's own copy,
    then derived copy -- an empty label on a bundle is absence, not an override.
    """
    bundles = [decl for decl in decls if decl.from_bundle]
    vms = [decl for decl in decls if not decl.from_bundle]
    vm_label, vm_description = _vm_fallback(vms)
    bundle_label, bundle_description = _bundle_fallback(bundles)
    return {
        "id": flag,
        "label": _first(*(b.label for b in bundles), vm_label, bundle_label),
        "description": _first(
            *(b.description for b in bundles),
            *(v.description for v in vms),
            vm_description,
            bundle_description,
        ),
        "default": _default_of(flag, decls),
    }


def render_feature_flags(spec: ScenarioSpec) -> str:
    """Render ``manifest/feature_flags.yml`` -- the deploy-time toggle list.

    Entries are sorted by ``id`` so the committed file does not churn when the
    canvas reorders nodes. Raises ``ValueError`` on a flag declared with two
    conflicting defaults, which would desync the TUI from the playbook guards.
    """
    grouped = _group(_declarations(spec))
    features = [_feature(flag, decls) for flag, decls in sorted(grouped.items())]
    header = (
        f"# Feature flags for {spec.name} -- optional components toggled at deploy time\n"
        "# via `-e INSTALL_<ID>=YES|NO`. Read by the deploy TUI; mirrored by the\n"
        '# `when: INSTALL_<ID> | default("YES"|"NO") | upper == "YES"` playbook guards.\n'
        "# Generated by the scenario renderer -- edit the scenario, not this file.\n"
    )
    body = yaml.dump(
        {"features": features},
        Dumper=_IndentedDumper,
        sort_keys=False,
        default_flow_style=False,
    )
    return f"---\n{header}\n{body}"
