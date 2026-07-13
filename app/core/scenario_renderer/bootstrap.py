"""stage_00 -- render a tier's per-VM vm-bootstrap call-sites.

One flag-gated ``core/vm.bootstrap`` import block per VM, mapping the VM's fields
onto the ``global_*`` var contract that bundle consumes.
"""

from __future__ import annotations

import yaml

from app.core.scenario_renderer._common import gate_expr
from app.core.scenario_renderer.registry import resolve_bundle_playbook
from app.core.scenario_renderer.types import VmSpec


def _bootstrap_block(vm: VmSpec) -> dict:
    block: dict = {"import_playbook": resolve_bundle_playbook("core/vm.bootstrap")}
    if vm.install_flag:
        block["when"] = (
            gate_expr(vm.install_flag, vm.install_default)
        )
    block["vars"] = {
        "global_vm_name": vm.vm_name,
        "global_vm_ssh_name": vm.ssh_name,
        "global_vm_id": vm.vm_id,
        "global_vm_description": vm.description,
        "global_vm_tag_name": vm.role,
        "global_vm_ci_ip": vm.ip,
        "global_template_vm_id": vm.template_vmid,
        "global_vm_net_virtio_bridge": vm.bridge,
        "global_vm_ci_ip_gw": vm.gateway,
        # without these the bundle falls back to a /24 on 1.1.1.1, so a canvas
        # drawing a /25 or /16 comes up misrouted and every stage_01 play is UNREACHABLE
        "global_vm_ci_netmask": vm.netmask,
        "global_vm_ci_dns_ips": vm.dns,
    }
    return block


def render_bootstrap_group(vms: list[VmSpec], group_id: str) -> str:
    """Render a tier's ``stage_00-vm_bootstrap/_<group_id>.yml``.

    Returns ``""`` for an empty VM list -- ``yaml.safe_dump([])`` is ``"[]\\n"``,
    which Ansible refuses to import ("a playbook must contain at least one play").
    The empty string is the caller's falsy signal to write no file and emit no
    ``import_playbook`` for this group.
    """
    if not vms:
        return ""
    blocks = [_bootstrap_block(vm) for vm in vms]
    return yaml.safe_dump(blocks, sort_keys=False, default_flow_style=False)
