"""stage_01 -- render one VM's software-attach file.

The VM already exists (stage_00 bootstrapped it); this file installs the software
bundles onto it. One flag-gated ``import_playbook`` block per attached bundle, in
declared order, each passing the VM's identity down to the bundle.

Three call-site contracts are enforced here, because each is a silent deploy
failure otherwise:

* the VM may itself be flag-gated -- a bundle targets ``hosts: r42.<vm_name>`` by
  name, so a bundle import on a gated-off VM (whose stage_00 never ran) would fire
  against a non-existent host and abort the deploy UNREACHABLE. The emitted guard
  therefore ANDs the VM's gate with the bundle's own gate;
* only VM-level bundles belong here -- a GROUP/XTIER bundle handed the VM's
  ``global_vm_ssh_name`` dies at deploy with its own target var undefined;
* bundles do not open their own firewall ports, so when any attached bundle
  declares ports the file is prefixed with an aggregated firewall play opening 22
  plus the union of those ports (the ``vuln_box_01.yml`` shape).
"""

from __future__ import annotations

import yaml

from app.core.scenario_renderer.registry import (
    bundle_kind,
    is_vm_level,
    resolve_bundle_playbook,
)
from app.core.scenario_renderer.types import BundleRef, VmSpec

#: Vars the VM owns. A bundle is a verb and owns no identity, so it may never set
#: these: retargeting them would install onto another host while still rendering
#: as plausible YAML. Caught at render time, before any Ansible touches Proxmox.
_VM_OWNED_VARS = ("global_vm_ssh_name", "global_vm_ci_ip")

#: Role that owns firewall configuration on a VM (see vuln_box_01.yml).
_FIREWALL_ROLE = "software.configure.firewalls"

#: Always kept open so the deployer can still reach the VM over SSH.
_SSH_PORT = 22


def _gate_expr(flag: str, default: str) -> str:
    """One ``INSTALL_<FLAG>``-style truthiness test, as Ansible reads it."""
    return f'INSTALL_{flag} | default("{default}") | upper == "YES"'


def _software_block(vm: VmSpec, ref: BundleRef) -> dict:
    if not is_vm_level(ref.name):
        raise ValueError(
            f"bundle {ref.name!r} on VM {vm.vm_name!r} is a {bundle_kind(ref.name).value!r} "
            f"bundle; only VM-level bundles attach to a VM (a group/xtier bundle needs a "
            f"group var the VM cannot supply and would fail undefined at deploy)"
        )

    hijacked = [key for key in _VM_OWNED_VARS if key in ref.vars]
    if hijacked:
        raise ValueError(
            f"bundle {ref.name!r} on VM {vm.vm_name!r} may not override VM-owned "
            f"vars: {', '.join(hijacked)}"
        )

    block: dict = {"import_playbook": resolve_bundle_playbook(ref.name)}
    gates = []
    if vm.install_flag:  # the VM's gate rides every bundle so nothing targets a gated-off VM
        gates.append(_gate_expr(vm.install_flag, vm.install_default))
    if ref.install_flag:
        gates.append(_gate_expr(ref.install_flag, ref.install_default))
    if gates:
        block["when"] = " and ".join(gates)
    block["vars"] = {
        "global_vm_ssh_name": vm.ssh_name,
        "global_vm_ci_ip": vm.ip,
        **ref.vars,
    }
    return block


def _firewall_play(vm: VmSpec, ports: list[int]) -> dict:
    """The aggregated firewall play prepended when bundles declare service ports.

    A play cannot carry a top-level ``when`` (Ansible rejects it), so a gated VM's
    gate rides the role entry instead, with ``gather_facts: false`` so a gated-off
    host is never even connected to.
    """
    play: dict = {
        "name": f"configure firewall - {vm.ssh_name} (aggregated from attached bundle ports)",
        "become": True,
        "hosts": vm.ssh_name,
    }
    if vm.install_flag:
        play["gather_facts"] = False
        play["roles"] = [{"role": _FIREWALL_ROLE, "when": _gate_expr(vm.install_flag, vm.install_default)}]
    else:
        play["roles"] = [_FIREWALL_ROLE]
    play["vars"] = {
        "firewall_rules": [{"ip": "all", "port": port, "protocol": "tcp"} for port in ports]
    }
    return play


def render_vm_software(vm: VmSpec) -> str:
    """Render a VM's ``stage_01-vm_configure/<vm_name>.yml``.

    Returns ``""`` for a VM with no bundles -- there is nothing to configure and
    no empty playbook Ansible will accept (both an empty file and a ``[]``
    document are fatal on import). The empty string is the caller's signal to
    write no file and emit no ``import_playbook`` for this VM.

    Raises ``ValueError`` if a non-VM-level bundle is attached or a bundle tries
    to override a VM-owned var, and propagates the registry's ``KeyError`` for an
    unknown bundle name.
    """
    if not vm.bundles:
        return ""
    blocks: list[dict] = []
    if any(ref.ports for ref in vm.bundles):
        ports = sorted({_SSH_PORT, *(port for ref in vm.bundles for port in ref.ports)})
        blocks.append(_firewall_play(vm, ports))
    blocks.extend(_software_block(vm, ref) for ref in vm.bundles)
    return yaml.safe_dump(blocks, sort_keys=False, default_flow_style=False)
