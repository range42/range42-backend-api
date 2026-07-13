"""Regression tests for defects found re-verifying the committed renderer.

Each test pins a deploy-correctness or safety property that the parallel-built
modules missed: an empty tier must not emit an unimportable ``[]`` wrapper, a VM
name must be a safe token (it becomes a filename and an ssh host), duplicate VM
names must be rejected, and an unknown bundle must fail like every other author
error (ValueError, so a route maps it to 400).
"""

from __future__ import annotations

import pytest
import yaml

from app.core.scenario_renderer import (
    BundleRef,
    ScenarioSpec,
    TierSpec,
    VmSpec,
    render_scenario,
    render_vm_software,
    resolve_bundle_playbook,
)


def _vm(name: str, vm_id: int, **kw) -> VmSpec:
    return VmSpec(
        vm_name=name,
        vm_id=vm_id,
        ip=f"192.168.142.{vm_id % 250}",
        role="admin",
        bridge="vmbr142",
        gateway="192.168.142.1",
        template_vmid=9232,
        **kw,
    )


def _tier(key: str, number: str, vms: tuple[VmSpec, ...]) -> TierSpec:
    return TierSpec(
        key=key,
        number=number,
        group_id=f"r42_{key}_group",
        active_group=f"r42_{key}_active",
        vms=vms,
    )


class TestEmptyTier:
    def test_a_vm_less_tier_is_not_wired_into_the_deploy(self):
        # a canvas may hold a drawn tier with no host yet; it must not emit a
        # `[]` stage-00 wrapper that main.yml then imports (fatal at parse).
        spec = ScenarioSpec(
            name="halfempty_lab",
            tiers=(
                _tier("admin", "02", (_vm("admin-wazuh", 1100),)),
                _tier("student", "03", ()),
            ),
        )
        files = render_scenario(spec)

        # nothing from the empty tier is emitted...
        assert not any(p.startswith("03_student_infrastructure/") for p in files)
        # ...and main.yml imports only files that exist
        for entry in yaml.safe_load(files["main.yml"]):
            assert entry["import_playbook"].lstrip("./") in files


class TestVmNameSafety:
    @pytest.mark.parametrize("bad", ["admin wazuh", "admin/evil", "../../etc/x", "Admin", "admin_wazuh"])
    def test_unsafe_vm_name_is_rejected_at_construction(self, bad):
        with pytest.raises(ValueError, match="vm_name"):
            _vm(bad, 1100)

    def test_valid_vm_names_are_accepted(self):
        for good in ("admin-wazuh", "vuln-box-01", "web3"):
            _vm(good, 1100)


class TestDuplicateVmName:
    def test_two_vms_sharing_a_name_are_rejected(self):
        # same name across tiers collapses on ssh host, inventory host, and the
        # stage_01 <name>.yml filename -- one VM's config would silently vanish.
        spec = ScenarioSpec(
            name="dup_lab",
            tiers=(
                _tier("admin", "02", (_vm("web", 1100),)),
                _tier("ctf", "04", (_vm("web", 1170),)),
            ),
        )
        with pytest.raises(ValueError, match="web"):
            render_scenario(spec)


class TestUnknownBundleErrorType:
    def test_unknown_bundle_raises_valueerror_like_every_other_author_error(self):
        with pytest.raises(ValueError, match="unknown bundle"):
            resolve_bundle_playbook("admin/software.install.nope")


class TestGateDeduplication:
    def test_shared_vm_and_bundle_flag_renders_one_clause_not_x_and_x(self):
        vm = _vm(
            "admin-wazuh",
            1100,
            install_flag="WAZUH",
            bundles=(BundleRef("admin/software.install.wazuh", install_flag="WAZUH", install_default="YES"),),
        )
        block = yaml.safe_load(render_vm_software(vm))[0]
        assert block["when"] == 'INSTALL_WAZUH | default("YES") | upper == "YES"'
