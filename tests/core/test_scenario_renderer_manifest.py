"""Scenario renderer -- manifest/scenario_vms.json (schema version 2).

The manifest is the scenario's machine-readable identity declaration: the
installer's scenario auto-discovery keys on its presence, the deployer CLI reads
``.vms[].vm_id`` / ``.vms[].ip`` to drive start/stop/snapshot/teardown, and the
repo-wide ``_reserved.json`` ledger is regenerated from every scenario's copy to
detect VMID/IP collisions. Schema drift breaks all three.
"""

import json

import pytest

from app.core.scenario_renderer.manifest import render_scenario_vms
from app.core.scenario_renderer.types import (
    ScenarioSpec,
    TemplateSpec,
    TierSpec,
    VmSpec,
)


def _vm(vm_id: int, vm_name: str, ip: str, role: str, bridge: str, **kw) -> VmSpec:
    """A VmSpec with the fields the manifest ignores filled in plausibly."""
    return VmSpec(
        vm_name=vm_name,
        vm_id=vm_id,
        ip=ip,
        role=role,
        bridge=bridge,
        gateway=kw.pop("gateway", "192.168.142.1"),
        template_vmid=kw.pop("template_vmid", 9232),
        **kw,
    )


def test_manifest_declares_schema_version_2_and_scenario_identity():
    spec = ScenarioSpec(name="demo_lab", description="Reference demo lab.")

    doc = json.loads(render_scenario_vms(spec))

    assert doc["version"] == 2
    assert doc["scenario"] == "demo_lab"
    assert doc["description"] == "Reference demo lab."


def test_every_vm_of_every_tier_is_declared_with_exactly_the_v2_vm_keys():
    # Two tiers -- the manifest is scenario-wide, it does not stop at the first tier.
    spec = ScenarioSpec(
        name="demo_lab",
        tiers=(
            TierSpec(
                key="admin",
                number="02",
                group_id="r42_admin_group",
                active_group="r42_admin_active",
                vms=(_vm(1100, "admin-wazuh", "192.168.142.100", "admin", "vmbr142"),),
            ),
            TierSpec(
                key="ctf",
                number="04",
                group_id="r42_ctf_group",
                active_group="r42_ctf_active",
                vms=(_vm(1170, "vuln-box-00", "192.168.144.170", "ctf", "vmbr144"),),
            ),
        ),
    )

    doc = json.loads(render_scenario_vms(spec))

    assert doc["vms"] == [
        {
            "vm_id": 1100,
            "vm_name": "admin-wazuh",
            "ip": "192.168.142.100",
            "role": "admin",
            "bridge": "vmbr142",
        },
        {
            "vm_id": 1170,
            "vm_name": "vuln-box-00",
            "ip": "192.168.144.170",
            "role": "ctf",
            "bridge": "vmbr144",
        },
    ]
    # vm_id is a JSON number: the CLI and the ledger compare it numerically.
    assert all(isinstance(entry["vm_id"], int) for entry in doc["vms"])


def test_vm_gated_off_by_default_is_still_declared_because_its_slot_is_reserved():
    """The manifest reserves identity, it does not report what this deploy creates.

    demo_lab ships exactly this shape: admin-misp / gitea / mattermost default to
    INSTALL_*=NO yet are listed, because their vm_id and IP are spoken for and the
    _reserved.json ledger must see them to catch a collision from another scenario.
    """
    spec = ScenarioSpec(
        name="demo_lab",
        tiers=(
            TierSpec(
                key="admin",
                number="02",
                group_id="r42_admin_group",
                active_group="r42_admin_active",
                vms=(
                    _vm(
                        1100,
                        "admin-wazuh",
                        "192.168.142.100",
                        "admin",
                        "vmbr142",
                        install_flag="WAZUH",
                        install_default="YES",
                    ),
                    _vm(
                        1112,
                        "admin-misp",
                        "192.168.142.112",
                        "admin",
                        "vmbr142",
                        install_flag="MISP",
                        install_default="NO",  # gated OFF -- still reserved
                    ),
                ),
            ),
        ),
    )

    doc = json.loads(render_scenario_vms(spec))

    assert [entry["vm_name"] for entry in doc["vms"]] == ["admin-wazuh", "admin-misp"]
    # ...and the manifest leaks no INSTALL_* gate: only the 5 identity keys.
    assert set(doc["vms"][1]) == {"vm_id", "vm_name", "ip", "role", "bridge"}


def test_templates_are_declared_with_exactly_the_v2_template_keys():
    spec = ScenarioSpec(
        name="demo_lab",
        templates=(
            TemplateSpec(
                vm_id=9901,
                vm_name="template-vm-nano",
                spec="1cpu/1gb/16gb",
                ip="192.168.140.201",
                bridge="vmbr140",
            ),
            TemplateSpec(
                vm_id=9232,
                vm_name="template-vm-medium-02-8g-64g",
                spec="2cpu/8gb/64gb",
                ip="192.168.140.232",
                bridge="vmbr140",
            ),
        ),
    )

    doc = json.loads(render_scenario_vms(spec))

    assert doc["templates"] == [
        {
            "vm_id": 9232,
            "vm_name": "template-vm-medium-02-8g-64g",
            "spec": "2cpu/8gb/64gb",
            "ip": "192.168.140.232",
            "bridge": "vmbr140",
        },
        {
            "vm_id": 9901,
            "vm_name": "template-vm-nano",
            "spec": "1cpu/1gb/16gb",
            "ip": "192.168.140.201",
            "bridge": "vmbr140",
        },
    ]
    assert all(isinstance(entry["vm_id"], int) for entry in doc["templates"])


def test_vms_are_ordered_by_vm_id_so_canvas_reordering_causes_no_git_churn():
    """Ordering rule: ascending ``vm_id``, scenario-wide (tier boundaries ignored).

    This file is committed. A canvas that reshuffles its nodes, or a tier that gains
    a VM, must not rewrite unrelated lines -- so the order is a function of identity,
    not of authoring order.
    """
    spec = ScenarioSpec(
        name="demo_lab",
        tiers=(
            # authored out of order, and the low vm_id sits in the *second* tier
            TierSpec(
                key="ctf",
                number="04",
                group_id="r42_ctf_group",
                active_group="r42_ctf_active",
                vms=(
                    _vm(1171, "vuln-box-01", "192.168.144.171", "ctf", "vmbr144"),
                    _vm(1170, "vuln-box-00", "192.168.144.170", "ctf", "vmbr144"),
                ),
            ),
            TierSpec(
                key="admin",
                number="02",
                group_id="r42_admin_group",
                active_group="r42_admin_active",
                vms=(_vm(1100, "admin-wazuh", "192.168.142.100", "admin", "vmbr142"),),
            ),
        ),
    )

    doc = json.loads(render_scenario_vms(spec))

    assert [entry["vm_id"] for entry in doc["vms"]] == [1100, 1170, 1171]


class TestSelfCollision:
    """A UI can now author these, so the renderer enforces _check_reserved.sh's rules
    up front rather than letting a broken manifest reach the ledger at commit time.
    """

    def test_duplicate_vm_id_between_vms_is_rejected(self):
        spec = ScenarioSpec(
            name="demo_lab",
            tiers=(
                TierSpec(
                    key="admin",
                    number="02",
                    group_id="r42_admin_group",
                    active_group="r42_admin_active",
                    vms=(
                        _vm(1100, "admin-wazuh", "192.168.142.100", "admin", "vmbr142"),
                        # same vm_id, different box, different tier-mate
                        _vm(1100, "admin-misp", "192.168.142.112", "admin", "vmbr142"),
                    ),
                ),
            ),
        )

        with pytest.raises(ValueError, match="duplicate vm_id"):
            render_scenario_vms(spec)

    def test_duplicate_vm_id_across_two_tiers_is_rejected(self):
        # all_vms flattens tiers -- the collision check must too
        spec = ScenarioSpec(
            name="demo_lab",
            tiers=(
                TierSpec(
                    key="admin",
                    number="02",
                    group_id="r42_admin_group",
                    active_group="r42_admin_active",
                    vms=(
                        _vm(1100, "admin-wazuh", "192.168.142.100", "admin", "vmbr142"),
                    ),
                ),
                TierSpec(
                    key="ctf",
                    number="04",
                    group_id="r42_ctf_group",
                    active_group="r42_ctf_active",
                    vms=(
                        _vm(1100, "vuln-box-00", "192.168.144.170", "ctf", "vmbr144"),
                    ),
                ),
            ),
        )

        with pytest.raises(ValueError, match="duplicate vm_id"):
            render_scenario_vms(spec)

    def test_duplicate_bridge_ip_pair_between_vms_is_rejected(self):
        spec = ScenarioSpec(
            name="demo_lab",
            tiers=(
                TierSpec(
                    key="admin",
                    number="02",
                    group_id="r42_admin_group",
                    active_group="r42_admin_active",
                    vms=(
                        _vm(1100, "admin-wazuh", "192.168.142.100", "admin", "vmbr142"),
                        # distinct vm_id, but the same address on the same bridge
                        _vm(1112, "admin-misp", "192.168.142.100", "admin", "vmbr142"),
                    ),
                ),
            ),
        )

        with pytest.raises(ValueError, match=r"duplicate \(bridge, ip\)"):
            render_scenario_vms(spec)

    def test_same_ip_on_a_different_bridge_is_allowed(self):
        # collision is on the (bridge, ip) pair -- subnets are per-bridge, so the
        # same last octet on another bridge is a legitimate, non-colliding address
        spec = ScenarioSpec(
            name="demo_lab",
            tiers=(
                TierSpec(
                    key="admin",
                    number="02",
                    group_id="r42_admin_group",
                    active_group="r42_admin_active",
                    vms=(
                        _vm(1100, "a", "192.168.142.100", "admin", "vmbr142"),
                        _vm(1170, "b", "192.168.142.100", "ctf", "vmbr144"),
                    ),
                ),
            ),
        )

        doc = json.loads(render_scenario_vms(spec))

        assert [entry["vm_id"] for entry in doc["vms"]] == [1100, 1170]

    def test_vm_id_shared_by_a_vm_and_a_template_is_rejected(self):
        spec = ScenarioSpec(
            name="demo_lab",
            tiers=(
                TierSpec(
                    key="admin",
                    number="02",
                    group_id="r42_admin_group",
                    active_group="r42_admin_active",
                    vms=(
                        _vm(9232, "admin-wazuh", "192.168.142.100", "admin", "vmbr142"),
                    ),
                ),
            ),
            templates=(
                TemplateSpec(
                    vm_id=9232,  # cross-role collision: the template already owns 9232
                    vm_name="template-vm-medium-02-8g-64g",
                    spec="2cpu/8gb/64gb",
                    ip="192.168.140.232",
                    bridge="vmbr140",
                ),
            ),
        )

        with pytest.raises(ValueError, match="vm_id"):
            render_scenario_vms(spec)


def test_rendered_text_round_trips_to_the_whole_v2_document():
    """End-to-end on a demo_lab slice: the text is JSON, and it is the entire schema."""
    spec = ScenarioSpec(
        name="demo_lab",
        description="Reference demo lab.",
        tiers=(
            TierSpec(
                key="admin",
                number="02",
                group_id="r42_admin_group",
                active_group="r42_admin_active",
                vms=(
                    _vm(
                        1100,
                        "admin-wazuh",
                        "192.168.142.100",
                        "admin",
                        "vmbr142",
                        install_flag="WAZUH",
                    ),
                    _vm(
                        1112,
                        "admin-misp",
                        "192.168.142.112",
                        "admin",
                        "vmbr142",
                        install_flag="MISP",
                        install_default="NO",
                    ),
                ),
            ),
            TierSpec(
                key="student",
                number="03",
                group_id="r42_student_group",
                active_group="r42_student_active",
                vms=(
                    _vm(
                        1160, "student-box-01", "192.168.143.160", "student", "vmbr143"
                    ),
                ),
            ),
        ),
        templates=(
            TemplateSpec(
                vm_id=9901,
                vm_name="template-vm-nano",
                spec="1cpu/1gb/16gb",
                ip="192.168.140.201",
                bridge="vmbr140",
            ),
        ),
    )

    text = render_scenario_vms(spec)

    assert json.loads(text) == {
        "scenario": "demo_lab",
        "version": 2,
        "description": "Reference demo lab.",
        "vms": [
            {
                "vm_id": 1100,
                "vm_name": "admin-wazuh",
                "ip": "192.168.142.100",
                "role": "admin",
                "bridge": "vmbr142",
            },
            {
                "vm_id": 1112,
                "vm_name": "admin-misp",
                "ip": "192.168.142.112",
                "role": "admin",
                "bridge": "vmbr142",
            },
            {
                "vm_id": 1160,
                "vm_name": "student-box-01",
                "ip": "192.168.143.160",
                "role": "student",
                "bridge": "vmbr143",
            },
        ],
        "templates": [
            {
                "vm_id": 9901,
                "vm_name": "template-vm-nano",
                "spec": "1cpu/1gb/16gb",
                "ip": "192.168.140.201",
                "bridge": "vmbr140",
            },
        ],
    }
    # committed file: newline-terminated, and rendering twice is byte-identical
    assert text.endswith("\n")
    assert render_scenario_vms(spec) == text
