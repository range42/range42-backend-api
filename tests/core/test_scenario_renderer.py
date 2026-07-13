"""Scenario renderer — canvas topology -> concrete bundle-scenario directory.

Golden-fixture-driven: the renderer must regenerate the per-VM stage_00
vm-bootstrap call-site blocks that scenarios like demo_lab hand-author today,
so the legacy scenario runner deploys the output unchanged (no _universal).
"""

import pytest
import yaml

from app.core.scenario_renderer import (
    BundleKind,
    VmSpec,
    bundle_kind,
    is_vm_level,
    render_bootstrap_group,
    resolve_bundle_playbook,
)


class TestBundleRegistry:
    """Bundles are addressed by their grammar name -- <tier>/<subject>.<verb>[.<object>].

    The playbooks repo has not finished migrating core/ onto the grammar, so the
    registry aliases a grammar name to its current on-disk path. When core is
    renamed, the alias goes away and the default resolution takes over.
    """

    def test_grammar_name_resolves_to_env_anchored_bundle_path(self):
        # admin/ already conforms: name maps straight through, no alias
        assert resolve_bundle_playbook("admin/software.install.wazuh") == (
            "{{ lookup('env', 'RANGE42_GITDIR__ROOT_DIR') }}/"
            "range42-playbooks/bundles/admin/software.install.wazuh/main.yml"
        )

    def test_unmigrated_core_bundle_resolves_through_its_alias(self):
        # core/ is pre-migration: vm.bootstrap still lives at the old nested path
        assert resolve_bundle_playbook("core/vm.bootstrap") == (
            "{{ lookup('env', 'RANGE42_GITDIR__ROOT_DIR') }}/"
            "range42-playbooks/bundles/core/proxmox/configure/vm-bootstrap/main.yml"
        )

    def test_unknown_bundle_is_rejected_rather_than_silently_rendered(self):
        with pytest.raises(ValueError, match="admin/software.install.nope"):
            resolve_bundle_playbook("admin/software.install.nope")

    def test_ctf_bundles_are_addressable_by_their_taxonomy_path(self):
        # ctf/ is exempt from the subject.verb.object grammar: a challenge is
        # identity-addressed (which CVE), not action-addressed.
        assert resolve_bundle_playbook("ctf/cve/web/tomcat/CVE-2025-24813") == (
            "{{ lookup('env', 'RANGE42_GITDIR__ROOT_DIR') }}/"
            "range42-playbooks/bundles/ctf/cve/web/tomcat/CVE-2025-24813/main.yml"
        )


class TestBundleKind:
    """A bundle's kind IS its call-site contract -- which var it needs to be told
    what to act on. This is what decides where a bundle may be attached, and it is
    the descriptor the UI palette needs to know what you can drop on what.
    """

    def test_vm_bundle_takes_a_single_host(self):
        # needs global_vm_ssh_name -> attaches to one VM
        assert bundle_kind("admin/software.install.gitea") is BundleKind.VM
        assert bundle_kind("ctf/cve/web/tomcat/CVE-2025-24813") is BundleKind.VM
        assert bundle_kind("core/vm.bootstrap") is BundleKind.VM

    def test_group_bundle_takes_a_target_group(self):
        # needs target_group -> attaches to a tier's baseline, never to a VM
        assert bundle_kind("core/system.baseline.default") is BundleKind.GROUP
        assert bundle_kind("core/network.baseline.ssh") is BundleKind.GROUP
        assert bundle_kind("core/software.install.tailscale") is BundleKind.GROUP

    def test_wazuh_agent_is_cross_tier_not_vm_level(self):
        # needs wazuh_clients_group -- a group assembled ACROSS tiers. This is why
        # demo_lab ends on a finalize step; it is the bundle's contract, not a quirk.
        assert bundle_kind("admin/software.install.wazuh-agent") is BundleKind.XTIER

    def test_infra_bundle_takes_no_host_parameter(self):
        # runs on proxmox, acts on the scenario itself
        assert bundle_kind("core/template.build.ubuntu-noble") is BundleKind.INFRA

    def test_attaching_a_group_bundle_to_a_vm_is_rejected(self):
        # the failure this prevents: the bundle reads an undefined target_group and
        # the play silently no-ops (or dies) at deploy time against a live range.
        assert is_vm_level("admin/software.install.gitea") is True
        assert is_vm_level("core/system.baseline.default") is False


def test_bootstrap_block_maps_vm_fields_to_global_vars():
    # One admin VM, mirroring demo_lab's admin-wazuh stage_00 block.
    vm = VmSpec(
        vm_name="admin-wazuh",
        vm_id=1100,
        ip="192.168.142.100",
        role="admin",
        bridge="vmbr142",
        gateway="192.168.142.1",
        template_vmid=9232,
        install_flag="WAZUH",
    )

    text = render_bootstrap_group([vm], group_id="r42_admin_group")
    plays = yaml.safe_load(text)

    assert isinstance(plays, list) and len(plays) == 1
    block = plays[0]

    # imports the shared vm-bootstrap bundle (not a per-VM hand-written play)
    assert block["import_playbook"].endswith(
        "range42-playbooks/bundles/core/proxmox/configure/vm-bootstrap/main.yml"
    )
    # gated by the VM's INSTALL_<FLAG>, case-insensitive, default preserved
    assert block["when"] == 'INSTALL_WAZUH | default("YES") | upper == "YES"'
    # every field maps onto the global_* var contract the bundle consumes
    assert block["vars"] == {
        "global_vm_name": "admin-wazuh",
        "global_vm_ssh_name": "r42.admin-wazuh",
        "global_vm_id": 1100,
        "global_vm_description": "",
        "global_vm_tag_name": "admin",
        "global_vm_ci_ip": "192.168.142.100",
        "global_template_vm_id": 9232,
        "global_vm_net_virtio_bridge": "vmbr142",
        "global_vm_ci_ip_gw": "192.168.142.1",
        # netmask/dns default to a /24 on 1.1.1.1 when the canvas doesn't override them
        "global_vm_ci_netmask": "24",
        "global_vm_ci_dns_ips": "1.1.1.1",
    }


def test_bootstrap_block_carries_non_default_netmask_and_dns():
    # a canvas drawing a /25 subnet on a custom resolver must reach cloud-init, or
    # the VM comes up with a /24 mask, misroutes, and every stage_01 play is UNREACHABLE.
    vm = VmSpec(
        vm_name="student-box",
        vm_id=1300,
        ip="192.168.143.10",
        role="student",
        bridge="vmbr143",
        gateway="192.168.143.1",
        template_vmid=9232,
        netmask="25",
        dns="10.0.0.1",
    )

    block = yaml.safe_load(render_bootstrap_group([vm], group_id="r42_student_group"))[0]

    assert block["vars"]["global_vm_ci_netmask"] == "25"
    assert block["vars"]["global_vm_ci_dns_ips"] == "10.0.0.1"


def test_bootstrap_group_with_no_vms_renders_the_empty_string_not_a_list():
    # yaml.safe_dump([]) is "[]\n"; Ansible rejects an import with no plays. "" is
    # the caller's falsy signal to write no file and emit no import_playbook.
    assert render_bootstrap_group([], group_id="r42_admin_group") == ""
