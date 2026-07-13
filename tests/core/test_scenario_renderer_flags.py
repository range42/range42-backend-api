"""manifest/feature_flags.yml -- the deploy-time toggle contract.

This file is read by the deploy TUI to render the checkbox modal and is mirrored
by the ``INSTALL_<ID>`` guards in the rendered playbooks, so it must list every
gate the scenario declares -- exactly once -- with the default the playbooks use.
"""

import pytest
import yaml

from app.core.scenario_renderer.flags import render_feature_flags
from app.core.scenario_renderer.types import BundleRef, ScenarioSpec, TierSpec, VmSpec


def _tier(*vms: VmSpec, baseline: tuple[BundleRef, ...] = ()) -> TierSpec:
    return TierSpec(
        key="admin",
        number="02",
        group_id="r42_admin_group",
        active_group="r42_admin_active",
        vms=vms,
        baseline_bundles=baseline,
    )


def _vm(name: str = "admin-wazuh", **kw) -> VmSpec:
    fields = {
        "vm_name": name,
        "vm_id": 1100,
        "ip": "192.168.142.100",
        "role": "admin",
        "bridge": "vmbr142",
        "gateway": "192.168.142.1",
        "template_vmid": 9232,
    }
    fields.update(kw)
    return VmSpec(**fields)


def _features(spec: ScenarioSpec) -> list[dict]:
    return yaml.safe_load(render_feature_flags(spec))["features"]


def test_bundle_install_flag_becomes_a_toggleable_feature():
    spec = ScenarioSpec(
        name="demo_lab",
        tiers=(
            _tier(
                _vm(
                    bundles=(
                        BundleRef(
                            name="admin/software.install.wazuh",
                            install_flag="WAZUH",
                            install_default="YES",
                            label="Wazuh stack - SIEM + agents services",
                            description="Deploys the wazuh-indexer / wazuh-server VM.",
                        ),
                    )
                )
            ),
        ),
    )

    assert _features(spec) == [
        {
            "id": "WAZUH",
            "label": "Wazuh stack - SIEM + agents services",
            "description": "Deploys the wazuh-indexer / wazuh-server VM.",
            "default": True,
        }
    ]


def test_vm_install_flag_is_toggleable_even_without_a_bundle():
    # The gate that decides whether the VM is created at all is a feature too.
    spec = ScenarioSpec(
        name="demo_lab",
        tiers=(_tier(_vm("admin-gitea", install_flag="GITEA", install_default="NO")),),
    )

    features = _features(spec)

    assert [f["id"] for f in features] == ["GITEA"]
    assert features[0]["default"] is False


def test_one_flag_gating_both_a_vm_and_its_bundle_is_emitted_once():
    # WAZUH gates the admin-wazuh VM *and* the wazuh bundle installed on it --
    # the TUI must show one checkbox, not two.
    wazuh = BundleRef(
        name="admin/software.install.wazuh",
        install_flag="WAZUH",
        install_default="YES",
        label="Wazuh stack - SIEM + agents services",
        description="Deploys the wazuh-indexer / wazuh-server VM.",
    )
    spec = ScenarioSpec(
        name="demo_lab",
        tiers=(_tier(_vm(install_flag="WAZUH", install_default="YES", bundles=(wazuh,))),),
    )

    features = _features(spec)

    assert [f["id"] for f in features] == ["WAZUH"]
    # the bundle carries the authored copy; the VM fallback must not win
    assert features[0]["label"] == "Wazuh stack - SIEM + agents services"
    assert features[0]["description"] == "Deploys the wazuh-indexer / wazuh-server VM."


def test_same_flag_with_two_different_defaults_is_rejected():
    # Silently picking one would desync the TUI checkbox from the playbook's
    # default(...) fallback -- the operator would see ON and get a skipped VM.
    spec = ScenarioSpec(
        name="demo_lab",
        tiers=(
            _tier(
                _vm(
                    install_flag="WAZUH",
                    install_default="YES",
                    bundles=(
                        BundleRef(
                            name="admin/software.install.wazuh",
                            install_flag="WAZUH",
                            install_default="NO",
                        ),
                    ),
                )
            ),
        ),
    )

    with pytest.raises(ValueError, match="WAZUH"):
        render_feature_flags(spec)


def test_defaults_agreeing_only_in_case_are_not_a_conflict():
    spec = ScenarioSpec(
        name="demo_lab",
        tiers=(
            _tier(
                _vm(
                    install_flag="WAZUH",
                    install_default="YES",
                    bundles=(
                        BundleRef(
                            name="admin/software.install.wazuh",
                            install_flag="WAZUH",
                            install_default="yes",
                        ),
                    ),
                )
            ),
        ),
    )

    assert _features(spec)[0]["default"] is True


def test_features_are_sorted_by_id_regardless_of_declaration_order():
    # The file is committed to git: reordering nodes on the canvas must not
    # rewrite it, so ordering is a property of the flag names, not of traversal.
    def spec_for(names: list[str]) -> ScenarioSpec:
        return ScenarioSpec(
            name="demo_lab",
            tiers=(
                _tier(*(_vm(f"admin-{n.lower()}", install_flag=n) for n in names)),
            ),
        )

    forward = render_feature_flags(spec_for(["WAZUH", "GITEA", "TAILSCALE", "MISP"]))
    shuffled = render_feature_flags(spec_for(["MISP", "TAILSCALE", "WAZUH", "GITEA"]))

    assert [f["id"] for f in yaml.safe_load(forward)["features"]] == [
        "GITEA",
        "MISP",
        "TAILSCALE",
        "WAZUH",
    ]
    assert forward == shuffled


def test_vm_only_flag_falls_back_to_copy_derived_from_the_gated_vm():
    spec = ScenarioSpec(
        name="demo_lab",
        tiers=(_tier(_vm("admin-gitea", install_flag="GITEA")),),
    )

    feature = _features(spec)[0]

    assert feature["label"] == "Deploy admin-gitea"
    assert feature["description"] == "Creates the admin-gitea VM."


def test_vm_only_flag_prefers_the_vms_own_description():
    spec = ScenarioSpec(
        name="demo_lab",
        tiers=(
            _tier(
                _vm(
                    "admin-gitea",
                    install_flag="GITEA",
                    description="Gitea - code services (vm_id 2125).",
                )
            ),
        ),
    )

    assert _features(spec)[0]["description"] == "Gitea - code services (vm_id 2125)."


def test_bundle_without_authored_copy_falls_back_to_its_bundle_name():
    spec = ScenarioSpec(
        name="demo_lab",
        tiers=(
            _tier(
                _vm(
                    bundles=(
                        BundleRef(
                            name="core/software.install.tailscale", install_flag="TAILSCALE"
                        ),
                    )
                )
            ),
        ),
    )

    feature = _features(spec)[0]

    assert feature["label"] == "Install core/software.install.tailscale"
    assert feature["description"] == "Runs the core/software.install.tailscale bundle."


def test_bundle_without_authored_copy_does_not_blank_out_the_vm_copy():
    # An empty label on the bundle is absence of copy, not an override.
    spec = ScenarioSpec(
        name="demo_lab",
        tiers=(
            _tier(
                _vm(
                    install_flag="WAZUH",
                    bundles=(
                        BundleRef(
                            name="admin/software.install.wazuh",
                            install_flag="WAZUH",
                            install_default="YES",
                        ),
                    ),
                )
            ),
        ),
    )

    assert _features(spec)[0]["label"] == "Deploy admin-wazuh"


def test_flag_gating_several_vms_names_them_all_in_the_fallback_copy():
    # DEPLOYER_UI gates 3 VMs: one checkbox, and the copy must not silently
    # name only the first of them.
    spec = ScenarioSpec(
        name="demo_lab",
        tiers=(
            _tier(
                _vm("deployer-api-gateway", vm_id=1101, install_flag="DEPLOYER_UI"),
                _vm("deployer-api-backend", vm_id=1102, install_flag="DEPLOYER_UI"),
                _vm("deployer-ui", vm_id=1103, install_flag="DEPLOYER_UI"),
            ),
        ),
    )

    feature = _features(spec)[0]

    assert feature["label"] == "Deploy deployer-api-gateway, deployer-api-backend, deployer-ui"
    assert feature["description"] == (
        "Creates the deployer-api-gateway, deployer-api-backend, deployer-ui VMs."
    )


def test_ungated_vms_and_bundles_produce_no_toggle():
    # install_flag=None means "always installed" -- there is nothing to check.
    spec = ScenarioSpec(
        name="demo_lab",
        tiers=(
            _tier(
                _vm(bundles=(BundleRef(name="core/system.baseline.default"),)),
                baseline=(BundleRef(name="core/network.baseline.ssh"),),
            ),
        ),
    )

    assert _features(spec) == []


def test_flag_shared_across_tiers_and_baseline_bundles_is_emitted_once():
    # WAZUH gates the admin-wazuh VM (admin tier) and the agent bundle installed
    # on every student box (student tier baseline).
    admin = _tier(
        _vm(
            install_flag="WAZUH",
            bundles=(
                BundleRef(
                    name="admin/software.install.wazuh",
                    install_flag="WAZUH",
                    install_default="YES",
                    label="Wazuh stack - SIEM + agents services",
                ),
            ),
        )
    )
    student = TierSpec(
        key="student",
        number="03",
        group_id="r42_student_group",
        active_group="r42_student_active",
        baseline_bundles=(
            BundleRef(
                name="admin/software.install.wazuh-agent",
                install_flag="WAZUH",
                install_default="YES",
            ),
        ),
    )
    spec = ScenarioSpec(name="demo_lab", tiers=(admin, student))

    features = _features(spec)

    assert [f["id"] for f in features] == ["WAZUH"]
    assert features[0]["label"] == "Wazuh stack - SIEM + agents services"


def test_scenario_without_flags_still_renders_a_valid_empty_contract():
    document = yaml.safe_load(render_feature_flags(ScenarioSpec(name="empty_lab")))

    assert document == {"features": []}


def test_output_is_a_yaml_document_whose_only_key_is_features():
    spec = ScenarioSpec(
        name="demo_lab",
        tiers=(_tier(_vm(install_flag="WAZUH")),),
    )

    text = render_feature_flags(spec)
    document = yaml.safe_load(text)

    assert text.startswith("---\n")
    assert list(document) == ["features"]
    assert set(document["features"][0]) == {"id", "label", "description", "default"}
    # indented sequence, like the hand-authored feature_flags.yml it sits beside
    assert "\n  - id: WAZUH\n" in text
