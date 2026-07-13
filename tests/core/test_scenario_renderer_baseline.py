"""Scenario renderer — the runtime "active" group and the tier baseline.

A gated-OFF VM is never created by stage_00, so any stage_01 play that targets it
would fail UNREACHABLE. Both halves of the fix are rendered here: an ``add_host``
play that builds the tier's runtime group from the INSTALL_* flags, and a baseline
that imports the tier's bundles against that group instead of the static inventory
group.
"""

import pytest
import yaml

from app.core.scenario_renderer.baseline import render_active_group, render_baseline
from app.core.scenario_renderer.types import BundleRef, TierSpec, VmSpec


def _vm(vm_name: str, **kw) -> VmSpec:
    defaults = dict(
        vm_id=1100,
        ip="192.168.142.100",
        role="admin",
        bridge="vmbr142",
        gateway="192.168.142.1",
        template_vmid=9232,
    )
    defaults.update(kw)
    return VmSpec(vm_name=vm_name, **defaults)


def _tier(*vms: VmSpec, **kw) -> TierSpec:
    defaults = dict(
        key="admin",
        number="02",
        group_id="r42_admin_group",
        active_group="r42_admin_active",
    )
    defaults.update(kw)
    return TierSpec(vms=vms, **defaults)


class TestRenderActiveGroup:
    """_build_<tier>_active_group.yml — membership decided at deploy time."""

    def test_emits_one_add_host_task_per_vm_in_a_single_play(self):
        tier = _tier(_vm("admin-wazuh"), _vm("admin-misp", vm_id=1101))

        plays = yaml.safe_load(render_active_group(tier))

        assert isinstance(plays, list) and len(plays) == 1
        play = plays[0]
        assert play["gather_facts"] is False
        assert [t["ansible.builtin.add_host"] for t in play["tasks"]] == [
            {"name": "r42.admin-wazuh", "groups": "r42_admin_active"},
            {"name": "r42.admin-misp", "groups": "r42_admin_active"},
        ]

    def test_gated_vm_joins_the_group_only_when_its_install_flag_is_yes(self):
        # membership mirrors stage_00's gate, defaults included
        tier = _tier(
            _vm("admin-wazuh", install_flag="WAZUH", install_default="YES"),
            _vm("admin-misp", vm_id=1101, install_flag="MISP", install_default="NO"),
        )

        tasks = yaml.safe_load(render_active_group(tier))[0]["tasks"]

        assert tasks[0]["when"] == 'INSTALL_WAZUH | default("YES") | upper == "YES"'
        assert tasks[1]["when"] == 'INSTALL_MISP | default("NO") | upper == "YES"'

    def test_ungated_vm_is_added_unconditionally(self):
        # no INSTALL flag => stage_00 always creates it => it is always a member
        tier = _tier(_vm("vuln-box-00", role="ctf", install_flag=None))

        task = yaml.safe_load(render_active_group(tier))[0]["tasks"][0]

        assert "when" not in task

    def test_play_is_a_runnable_named_play_that_reports_no_change(self):
        tier = _tier(_vm("admin-wazuh", install_flag="WAZUH"))

        play = yaml.safe_load(render_active_group(tier))[0]

        # the add_host tasks need a host to run on; proxmox is always in inventory
        assert play["hosts"] == "proxmox"
        assert "r42_admin_active" in play["name"]
        # building an in-memory group is not a change to the infrastructure
        assert all(t["changed_when"] is False for t in play["tasks"])
        assert all(t["name"] for t in play["tasks"])

    def test_tier_with_no_vms_still_renders_a_loadable_play(self):
        plays = yaml.safe_load(render_active_group(_tier()))

        assert isinstance(plays, list) and len(plays) == 1
        assert plays[0]["tasks"] == []


class TestRenderBaseline:
    """stage_01-vm_configure/_baseline_<tier>.yml — bundles run on the active group."""

    def test_imports_each_baseline_bundle_in_order_against_the_active_group(self):
        # demo_lab's admin baseline: packages+dotfiles, then ssh firewall
        tier = _tier(
            baseline_bundles=(
                BundleRef(name="core/system.baseline.default"),
                BundleRef(name="core/network.baseline.ssh"),
            ),
        )

        blocks = yaml.safe_load(render_baseline(tier))

        assert [b["import_playbook"] for b in blocks] == [
            "{{ lookup('env', 'RANGE42_GITDIR__ROOT_DIR') }}"
            "/range42-playbooks/bundles/core/system-baseline-default/main.yml",
            "{{ lookup('env', 'RANGE42_GITDIR__ROOT_DIR') }}"
            "/range42-playbooks/bundles/core/network-baseline-ssh/main.yml",
        ]
        # the whole point: never the static group, only the VMs stage_00 created
        assert all(b["vars"] == {"target_group": "r42_admin_active"} for b in blocks)

    def test_bundle_vars_ride_along_with_the_target_group(self):
        # demo_lab's tailscale baseline bundle carries its own vars
        tier = _tier(
            baseline_bundles=(
                BundleRef(
                    name="core/software.install.tailscale",
                    vars={"tailscale_hostnames": ["admin-wazuh"]},
                ),
            ),
        )

        block = yaml.safe_load(render_baseline(tier))[0]

        assert block["vars"] == {
            "target_group": "r42_admin_active",
            "tailscale_hostnames": ["admin-wazuh"],
        }

    def test_gated_bundle_carries_its_install_guard_and_ungated_one_does_not(self):
        tier = _tier(
            baseline_bundles=(
                BundleRef(
                    name="core/software.install.tailscale",
                    install_flag="TAILSCALE",
                    install_default="NO",
                ),
                BundleRef(name="core/system.baseline.default"),
            ),
        )

        gated, ungated = yaml.safe_load(render_baseline(tier))

        assert gated["when"] == 'INSTALL_TAILSCALE | default("NO") | upper == "YES"'
        assert "when" not in ungated

    def test_tier_with_no_baseline_bundles_renders_the_empty_string_not_a_list(self):
        # yaml.safe_dump([]) is "[]\n", which Ansible rejects with "a playbook must
        # contain at least one play". "" is falsy, so the caller skips the import;
        # "[]\n" is truthy and would be imported and blow up at deploy.
        assert render_baseline(_tier()) == ""

    def test_vm_level_bundle_in_baseline_is_rejected(self):
        # baseline bundles run against target_group; a VM-level bundle reads
        # global_vm_ssh_name and would silently target nothing at deploy time.
        tier = _tier(baseline_bundles=(BundleRef(name="admin/software.install.wazuh"),))

        with pytest.raises(ValueError, match="admin/software.install.wazuh"):
            render_baseline(tier)
