"""Shared input contract for the scenario renderer.

A *bundle* is a verb: a reusable action that takes its target as a parameter and
owns no identity. A *scenario* is a noun: a deployable composition that creates
targets and owns their identity (vmid, ip, bridge) plus the INSTALL_* flags that
gate them. The canvas authors a scenario; these types are its resolved form.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class BundleRef:
    """A bundle attached to a VM (stage_01) or to a tier's group (baseline).

    ``name`` is the bundle grammar name -- ``<tier>/<subject>.<verb>[.<object>]``
    -- resolved to a path by the registry, never a physical path.

    ``ports`` are the TCP ports the bundle's service listens on. Bundles do NOT
    open their own firewall ports (the CTF ones carry a TODO saying as much), so
    the VM's stage_01 file must open the union of its bundles' ports up front --
    otherwise the service deploys fine and is simply unreachable.
    """

    name: str
    install_flag: str | None = None  # None => always installed, no gate
    install_default: str = "NO"
    label: str = ""
    description: str = ""
    vars: dict[str, str] = field(default_factory=dict)
    ports: tuple[int, ...] = ()


@dataclass(frozen=True)
class VmSpec:
    """One VM, resolved from a canvas host node."""

    vm_name: str
    vm_id: int
    ip: str
    role: str  # proxmox tag: admin | student | ctf | team
    bridge: str
    gateway: str
    template_vmid: int
    install_flag: str | None = None
    install_default: str = "YES"
    description: str = ""
    bundles: tuple[BundleRef, ...] = ()  # stage_01 software bundles on this VM
    netmask: str = "24"
    dns: str = "1.1.1.1"

    @property
    def ssh_name(self) -> str:
        return f"r42.{self.vm_name}"


@dataclass(frozen=True)
class CrossTierGroup:
    """A group assembled ACROSS tiers, and the XTIER bundle that runs against it.

    Some bundles act on a set of hosts that no single tier owns -- the canonical
    case is the Wazuh agent, which enrols clients drawn from every tier and must
    run after the Wazuh server exists. That is why a scenario ends on a finalize
    step: it is the bundle's call-site contract, not a quirk of demo_lab.

    ``group_var`` is the variable the bundle reads to find its targets (e.g.
    ``wazuh_clients_group``). Members keep their own INSTALL gate, so a member
    whose VM was never created is never enrolled.
    """

    group_id: str
    group_var: str
    bundle: BundleRef
    members: tuple[VmSpec, ...] = ()


@dataclass(frozen=True)
class TemplateSpec:
    """A Proxmox template the scenario depends on (01_templates-bootstrap).

    ``spec`` is the hardware size (``"2cpu/8gb/64gb"``); ``os_family`` names the
    template-build bundle that provisions it (``core/template.build.<os_family>``).
    The build bundle reads the manifest and materialises every template row of its
    family, so the bootstrap step imports one bundle per distinct family, not one
    per template.
    """

    vm_id: int
    vm_name: str
    spec: str
    ip: str
    bridge: str
    os_family: str = "ubuntu-noble"


@dataclass(frozen=True)
class TierSpec:
    """One infrastructure tier -- the 0N_<key>_infrastructure directory."""

    key: str  # admin | student | ctf | team
    number: str  # directory prefix: "02", "03", "04"
    group_id: str  # static inventory group, e.g. r42_admin_group
    active_group: str  # runtime group built from INSTALL flags, e.g. r42_admin_active
    vms: tuple[VmSpec, ...] = ()
    baseline_bundles: tuple[BundleRef, ...] = ()


SCENARIO_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class ScenarioSpec:
    """The whole scenario: what the renderer turns into a directory.

    ``name`` is load-bearing far beyond cosmetics. The deployer-cli derives the
    scenario name from the directory basename and looks for ``<name>.setup.sh``
    inside it, and the TUI parses workspaces with ``^(.+)-([a-z][a-z0-9_]*)$``.
    A hyphenated or title-cased name (the natural output of a UI project title)
    makes the workspace unparseable and the deploy script unfindable -- so the
    name is validated here, at construction, rather than failing on the range.
    """

    name: str  # snake_case, nominal, no verb
    description: str = ""
    tiers: tuple[TierSpec, ...] = ()
    templates: tuple[TemplateSpec, ...] = ()
    finalize: tuple[CrossTierGroup, ...] = ()
    codename: str = ""
    proxmox_address: str = ""

    def __post_init__(self) -> None:
        if not SCENARIO_NAME_RE.match(self.name):
            raise ValueError(
                f"invalid scenario name {self.name!r}: must be snake_case matching "
                f"{SCENARIO_NAME_RE.pattern} (lowercase, starts with a letter, "
                "underscores only). The deployer-cli derives <name>.setup.sh and the "
                "TUI parses the workspace name from it, so hyphens and uppercase break "
                "the deploy."
            )

    @property
    def all_vms(self) -> tuple[VmSpec, ...]:
        return tuple(vm for tier in self.tiers for vm in tier.vms)
