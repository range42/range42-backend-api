"""Scenario renderer — the orchestration playbooks (main.yml + per-tier stages).

These are the files that decide *order*, and order is the whole point: every
tier's stage_00 (create all VMs) must run before ANY tier's stage_01 (configure
them), because a cross-tier configure step — a wazuh agent install that has to
reach the wazuh server in another tier — only works once every VM exists and is
baselined. That global stage discipline is why main.yml imports per-tier STAGE
files rather than per-tier ``_main.yml`` wrappers, and why the XTIER finalize is
emitted last of all.

Reference shapes: demo_lab's ``main.yml``, ``01_templates-bootstrap/_main.yml``,
``02_admin_infrastructure/_main_stage_0{0,1}.yml``,
``_build_wazuh_clients_active_group.yml`` and
``stage_01-vm_configure/_finalize-baseline-admin_wazuh_client.yml``.
"""

import yaml

from app.core.scenario_renderer.orchestration import (
    finalize_group_build_filename,
    finalize_import_filename,
    render_finalize,
    render_finalize_group,
    render_main,
    render_templates_bootstrap,
    render_tier_stage00,
    render_tier_stage01,
)
from app.core.scenario_renderer.types import (
    BundleRef,
    CrossTierGroup,
    ScenarioSpec,
    TemplateSpec,
    TierSpec,
    VmSpec,
)


def _template(**kw) -> TemplateSpec:
    defaults = dict(
        vm_id=9232,
        vm_name="ubuntu-noble",
        spec="2cpu/8gb/64gb",
        os_family="ubuntu-noble",
        ip="192.168.142.232",
        bridge="vmbr142",
    )
    defaults.update(kw)
    return TemplateSpec(**defaults)


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


def _admin_tier(*vms: VmSpec, **kw) -> TierSpec:
    defaults = dict(
        key="admin",
        number="02",
        group_id="r42_admin_group",
        active_group="r42_admin_active",
    )
    defaults.update(kw)
    return TierSpec(vms=vms, **defaults)


def _ctf_tier(*vms: VmSpec, **kw) -> TierSpec:
    defaults = dict(
        key="ctf",
        number="04",
        group_id="r42_vuln_box_group",
        active_group="r42_vuln_box_active",
    )
    defaults.update(kw)
    return TierSpec(vms=vms, **defaults)


def _baseline_bundle() -> BundleRef:
    # a GROUP-kind bundle, the only kind a tier baseline accepts
    return BundleRef(name="core/system.baseline.default")


def _wazuh_group(*members: VmSpec, **kw) -> CrossTierGroup:
    # mirrors demo_lab's cross-tier wazuh-clients finalize
    defaults = dict(
        group_id="r42_demo_lab_wazuh_clients_active",
        group_var="wazuh_clients_group",
        bundle=BundleRef(
            name="admin/software.install.wazuh-agent",
            install_flag="WAZUH",
            install_default="YES",
            vars={"global_vm_ci_ip": "192.168.142.100"},
        ),
    )
    defaults.update(kw)
    return CrossTierGroup(members=members, **defaults)


def _imports(rendered: str) -> list[str]:
    return [play["import_playbook"] for play in yaml.safe_load(rendered)]


class TestRenderMain:
    """main.yml — the scenario entrypoint the Ansible runner executes."""

    def test_templates_bootstrap_is_imported_first(self):
        spec = ScenarioSpec(name="demo_lab", templates=(_template(),))

        assert _imports(render_main(spec))[0] == "./01_templates-bootstrap/_main.yml"

    def test_every_tier_contributes_a_stage_00_then_a_stage_01_in_tier_order(self):
        # mirrors demo_lab: 02_admin + 04_ctf, both stages, tier order preserved
        spec = ScenarioSpec(name="demo_lab", tiers=(_admin_tier(), _ctf_tier()))

        assert _imports(render_main(spec)) == [
            "./02_admin_infrastructure/_main_stage_00.yml",
            "./04_ctf_infrastructure/_main_stage_00.yml",
            "./02_admin_infrastructure/_main_stage_01.yml",
            "./04_ctf_infrastructure/_main_stage_01.yml",
        ]

    def test_every_tier_stage_00_runs_before_any_tier_stage_01(self):
        """The whole reason main.yml imports STAGE files, not per-tier _main.yml.

        A cross-tier configure step (wazuh agent on another tier's VMs, reaching
        the wazuh server) needs every VM created and baselined first. Grouping the
        imports per tier — stage_00 then stage_01 for tier 02, then the same for
        tier 04 — would configure tier 02 while tier 04's VMs do not yet exist.
        """
        spec = ScenarioSpec(
            name="demo_lab",
            templates=(_template(),),
            tiers=(
                _admin_tier(_vm("admin-wazuh")),
                _ctf_tier(_vm("vuln-box-00", role="ctf")),
                TierSpec(
                    key="student",
                    number="03",
                    group_id="r42_student_box_group",
                    active_group="r42_student_box_active",
                ),
            ),
        )

        imports = _imports(render_main(spec))
        last_stage_00 = max(
            i for i, p in enumerate(imports) if p.endswith("_main_stage_00.yml")
        )
        first_stage_01 = min(
            i for i, p in enumerate(imports) if p.endswith("_main_stage_01.yml")
        )

        assert last_stage_00 < first_stage_01
        # and every tier is actually represented in both phases
        assert sum(p.endswith("_main_stage_00.yml") for p in imports) == 3
        assert sum(p.endswith("_main_stage_01.yml") for p in imports) == 3

    def test_scenario_without_templates_does_not_import_the_bootstrap_tier(self):
        # a scenario reusing templates another scenario built has no 01_ directory
        spec = ScenarioSpec(name="reuse_lab", tiers=(_admin_tier(),))

        assert _imports(render_main(spec)) == [
            "./02_admin_infrastructure/_main_stage_00.yml",
            "./02_admin_infrastructure/_main_stage_01.yml",
        ]

    def test_imports_stay_relative_to_the_scenario_root(self):
        # portability: the rendered directory is self-contained, so intra-scenario
        # imports are never env-anchored (that is only for bundles) nor absolute
        spec = ScenarioSpec(
            name="demo_lab",
            templates=(_template(),),
            tiers=(_admin_tier(), _ctf_tier()),
            finalize=(_wazuh_group(_vm("vuln-box-00", role="ctf")),),
        )

        for path in _imports(render_main(spec)):
            assert path.startswith("./")
            assert "lookup(" not in path
            assert "RANGE42_GITDIR__ROOT_DIR" not in path

    def test_finalize_group_build_then_import_come_after_all_stage_01(self):
        """The XTIER finalize must run dead last (see module docstring).

        A wazuh server enrols agents drawn from every tier; the build-group play
        and the agent install can only fire once every tier's stage_01 has run and
        the server is reachable. So both finalize imports must sit after the last
        stage_01 import — emitting them anywhere earlier re-introduces the
        cross-tier ordering bug this whole file exists to prevent.
        """
        group = _wazuh_group(_vm("vuln-box-00", role="ctf"))
        spec = ScenarioSpec(
            name="demo_lab",
            tiers=(_admin_tier(_vm("admin-wazuh")), _ctf_tier(_vm("vuln-box-00", role="ctf"))),
            finalize=(group,),
        )

        imports = _imports(render_main(spec))
        last_stage_01 = max(
            i for i, p in enumerate(imports) if p.endswith("_main_stage_01.yml")
        )
        build = imports.index(f"./{finalize_group_build_filename(group)}")
        finalize = imports.index(f"./{finalize_import_filename(group)}")

        assert last_stage_01 < build < finalize
        assert imports[build] == "./_build_demo_lab_wazuh_clients_active_group.yml"
        assert imports[finalize] == "./_finalize_demo_lab_wazuh_clients.yml"

    def test_scenario_without_finalize_emits_no_finalize_imports(self):
        # a scenario with no wazuh-agent needs neither build-group nor finalize
        spec = ScenarioSpec(name="demo_lab", tiers=(_admin_tier(_vm("admin-wazuh")),))

        imports = _imports(render_main(spec))

        assert not any("_build_" in p for p in imports)
        assert not any("_finalize" in p for p in imports)


class TestRenderTemplatesBootstrap:
    """01_templates-bootstrap/_main.yml — provisions the shared Proxmox templates."""

    def test_imports_the_template_build_bundle_for_each_distinct_family(self):
        spec = ScenarioSpec(
            name="demo_lab",
            templates=(
                _template(vm_id=9232, os_family="ubuntu-noble"),
                _template(vm_id=9200, vm_name="alpine", os_family="alpine"),
            ),
        )

        imports = _imports(render_templates_bootstrap(spec))

        assert any(p.endswith("templates/ubuntu_noble/main.yml") for p in imports)
        assert any(p.endswith("templates/alpine/main.yml") for p in imports)

    def test_repeated_family_yields_a_single_build_import(self):
        # one bundle enumerates all its vmids from the manifest, so two ubuntu
        # templates need one import, not two
        spec = ScenarioSpec(
            name="demo_lab",
            templates=(
                _template(vm_id=9232, os_family="ubuntu-noble"),
                _template(vm_id=9233, vm_name="ubuntu-noble-b", os_family="ubuntu-noble"),
            ),
        )

        imports = _imports(render_templates_bootstrap(spec))

        assert sum(p.endswith("ubuntu_noble/main.yml") for p in imports) == 1

    def test_downloads_cloudinit_sources_before_building(self):
        spec = ScenarioSpec(name="demo_lab", templates=(_template(),))

        imports = _imports(render_templates_bootstrap(spec))

        assert imports[0].endswith("_main_download_cloudinit_files.yml")

    def test_build_import_carries_the_scenario_manifest_path(self):
        # the bundle enumerates which template vmids to build from the manifest
        spec = ScenarioSpec(name="demo_lab", templates=(_template(),))

        plays = yaml.safe_load(render_templates_bootstrap(spec))
        build = next(p for p in plays if p["import_playbook"].endswith("ubuntu_noble/main.yml"))

        assert build["vars"]["manifest_path"].endswith(
            "scenarios/demo_lab/manifest/scenario_vms.json"
        )

    def test_bundle_imports_are_env_anchored(self):
        # unlike main.yml, this file imports bundles, which must be env-anchored so
        # the rendered scenario finds the playbooks repo from anywhere
        spec = ScenarioSpec(name="demo_lab", templates=(_template(),))

        for path in _imports(render_templates_bootstrap(spec)):
            assert "RANGE42_GITDIR__ROOT_DIR" in path


class TestRenderTierStage00:
    """<tier>/_main_stage_00.yml — the tier's slice of the global create phase."""

    def test_imports_the_tier_group_bootstrap_file(self):
        # mirrors demo_lab 02_admin_infrastructure/_main_stage_00.yml
        tier = _admin_tier(_vm("admin-wazuh"))

        assert _imports(render_tier_stage00(tier)) == [
            "./stage_00-vm_bootstrap/_r42_admin_group.yml"
        ]

    def test_group_file_is_named_after_the_tier_group_id_not_its_key(self):
        # demo_lab's ctf tier bootstraps _r42_vuln_box_group.yml, not _r42_ctf_group.yml
        tier = _ctf_tier(_vm("vuln-box-00", role="ctf"))

        assert _imports(render_tier_stage00(tier)) == [
            "./stage_00-vm_bootstrap/_r42_vuln_box_group.yml"
        ]

    def test_tier_with_no_vms_imports_no_bootstrap_file(self):
        # render_bootstrap_group returns "" for an empty tier, so nothing is written
        # and importing "./stage_00-vm_bootstrap/_<group>.yml" would be a parse error
        plays = yaml.safe_load(render_tier_stage00(_admin_tier()))

        assert plays == []


class TestRenderTierStage01:
    """<tier>/_main_stage_01.yml — the tier's slice of the global configure phase."""

    def test_builds_the_active_group_then_baselines_it_then_configures_each_vm(self):
        # mirrors demo_lab 02_admin_infrastructure/_main_stage_01.yml
        tier = _admin_tier(
            _vm(
                "admin-wazuh", bundles=(BundleRef(name="admin/software.install.wazuh"),)
            ),
            _vm(
                "admin-misp",
                vm_id=1101,
                bundles=(BundleRef(name="admin/software.install.misp"),),
            ),
            baseline_bundles=(_baseline_bundle(),),
        )

        assert _imports(render_tier_stage01(tier)) == [
            "./_build_admin_active_group.yml",
            "./stage_01-vm_configure/_baseline_admin.yml",
            "./stage_01-vm_configure/admin-wazuh.yml",
            "./stage_01-vm_configure/admin-misp.yml",
        ]

    def test_active_group_is_built_before_the_baseline_that_targets_it(self):
        """_baseline_<key>.yml runs against the tier's runtime active group.

        The group is an in-memory add_host group, so the play that builds it must
        precede the baseline. Building it later (e.g. from main.yml, after the
        stage_01 imports) would leave the baseline with zero hosts and silently
        skip every VM in the tier.
        """
        tier = _admin_tier(_vm("admin-wazuh"), baseline_bundles=(_baseline_bundle(),))

        imports = _imports(render_tier_stage01(tier))

        assert imports.index("./_build_admin_active_group.yml") < imports.index(
            "./stage_01-vm_configure/_baseline_admin.yml"
        )

    def test_vm_without_software_bundles_is_not_imported(self):
        # stage_01 renders no <vm_name>.yml for a bundle-less VM, so importing one
        # would be a hard "file not found" at playbook parse time
        tier = _admin_tier(
            _vm(
                "admin-wazuh", bundles=(BundleRef(name="admin/software.install.wazuh"),)
            ),
            _vm("plain-box", vm_id=1102),  # baseline only, no software
        )

        imports = _imports(render_tier_stage01(tier))

        assert "./stage_01-vm_configure/admin-wazuh.yml" in imports
        assert "./stage_01-vm_configure/plain-box.yml" not in imports

    def test_tier_without_baseline_bundles_imports_no_baseline_file(self):
        # render_baseline returns "" when the tier declares no baseline bundles, so
        # importing "./stage_01-vm_configure/_baseline_<key>.yml" would be fatal
        tier = _admin_tier(_vm("admin-wazuh"))  # no baseline_bundles

        imports = _imports(render_tier_stage01(tier))

        assert imports == ["./_build_admin_active_group.yml"]

    def test_bare_tier_still_builds_its_active_group(self):
        # the active group is always non-empty (add_host play), even with no vms
        plays = yaml.safe_load(render_tier_stage01(_ctf_tier()))

        assert [p["import_playbook"] for p in plays] == [
            "./_build_ctf_active_group.yml"
        ]


class TestFinalizeFilenames:
    """The two finalize files are named by pure function of the group id."""

    def test_build_group_filename_strips_prefix_and_active_suffix(self):
        group = _wazuh_group()

        assert (
            finalize_group_build_filename(group)
            == "_build_demo_lab_wazuh_clients_active_group.yml"
        )

    def test_finalize_import_filename_strips_prefix_and_active_suffix(self):
        group = _wazuh_group()

        assert finalize_import_filename(group) == "_finalize_demo_lab_wazuh_clients.yml"


class TestRenderFinalizeGroup:
    """_build_<key>_active_group.yml — the cross-tier runtime membership."""

    def test_builds_an_add_host_play_on_proxmox(self):
        group = _wazuh_group(_vm("vuln-box-00", role="ctf"))

        plays = yaml.safe_load(render_finalize_group(group))

        assert len(plays) == 1
        assert plays[0]["hosts"] == "proxmox"
        assert plays[0]["gather_facts"] is False

    def test_every_member_is_added_to_the_group(self):
        group = _wazuh_group(
            _vm("admin-misp", install_flag="MISP", install_default="NO"),
            _vm("vuln-box-00", role="ctf"),
        )

        tasks = yaml.safe_load(render_finalize_group(group))[0]["tasks"]
        added = [
            (t["ansible.builtin.add_host"]["name"], t["ansible.builtin.add_host"]["groups"])
            for t in tasks
        ]

        assert added == [
            ("r42.admin-misp", "r42_demo_lab_wazuh_clients_active"),
            ("r42.vuln-box-00", "r42_demo_lab_wazuh_clients_active"),
        ]

    def test_member_with_install_flag_is_gated_and_flagless_member_is_not(self):
        group = _wazuh_group(
            _vm("admin-misp", install_flag="MISP", install_default="NO"),
            _vm("vuln-box-00", role="ctf"),  # ctf VMs are always created — no gate
        )

        tasks = yaml.safe_load(render_finalize_group(group))[0]["tasks"]
        by_host = {t["ansible.builtin.add_host"]["name"]: t for t in tasks}

        assert by_host["r42.admin-misp"]["when"] == (
            'INSTALL_MISP | default("NO") | upper == "YES"'
        )
        assert "when" not in by_host["r42.vuln-box-00"]


class TestRenderFinalize:
    """_finalize_<key>.yml — the XTIER bundle run against the assembled group."""

    def test_imports_the_xtier_bundle_pointed_at_the_group(self):
        group = _wazuh_group()

        plays = yaml.safe_load(render_finalize(group))

        assert len(plays) == 1
        assert plays[0]["import_playbook"].endswith(
            "admin/software.install.wazuh-agent/main.yml"
        )
        assert plays[0]["vars"]["wazuh_clients_group"] == "r42_demo_lab_wazuh_clients_active"

    def test_bundle_install_flag_gates_the_import(self):
        group = _wazuh_group()

        block = yaml.safe_load(render_finalize(group))[0]

        assert block["when"] == 'INSTALL_WAZUH | default("YES") | upper == "YES"'

    def test_bundle_own_vars_are_passed_through(self):
        group = _wazuh_group()

        block = yaml.safe_load(render_finalize(group))[0]

        assert block["vars"]["global_vm_ci_ip"] == "192.168.142.100"

    def test_bundle_without_install_flag_is_unconditional(self):
        group = _wazuh_group(
            bundle=BundleRef(name="admin/software.install.wazuh-agent")
        )

        block = yaml.safe_load(render_finalize(group))[0]

        assert "when" not in block
