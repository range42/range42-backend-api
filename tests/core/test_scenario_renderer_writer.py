"""The writer assembles every renderer into a complete scenario directory.

The acceptance bar: the emitted path set must satisfy the deployer-cli's MANDATORY
contract (verified against range42-context) -- the four templates/*.j2 discovery
gate, the manifest, and the five lifecycle scripts named for the scenario -- and
must never emit a secrets/ dir (the installer symlinks that in and would delete a
real one).
"""

from __future__ import annotations

import yaml

from app.core.scenario_renderer import (
    BundleRef,
    ScenarioSpec,
    TemplateSpec,
    TierSpec,
    VmSpec,
    render_scenario,
)


def _demo_admin_tier() -> TierSpec:
    wazuh = VmSpec(
        vm_name="admin-wazuh",
        vm_id=1100,
        ip="192.168.142.100",
        role="admin",
        bridge="vmbr142",
        gateway="192.168.142.1",
        template_vmid=9232,
        install_flag="WAZUH",
        bundles=(
            BundleRef(
                name="admin/software.install.wazuh",
                install_flag="WAZUH",
                install_default="YES",
            ),
        ),
    )
    return TierSpec(
        key="admin",
        number="02",
        group_id="r42_admin_group",
        active_group="r42_admin_active",
        vms=(wazuh,),
        baseline_bundles=(BundleRef(name="core/system.baseline.default"),),
    )


def _spec() -> ScenarioSpec:
    return ScenarioSpec(
        name="acme_lab",
        description="a generated lab",
        tiers=(_demo_admin_tier(),),
        templates=(
            TemplateSpec(
                vm_id=9232,
                vm_name="template-vm-medium-02-8g-64g",
                spec="2cpu/8gb/64gb",
                ip="192.168.140.232",
                bridge="vmbr140",
            ),
        ),
        codename="ACME",
        proxmox_address="192.168.42.1",
    )


def test_writer_emits_the_deployer_cli_mandatory_fileset():
    files = render_scenario(_spec())

    required = {
        "manifest/scenario_vms.json",
        "manifest/feature_flags.yml",
        "templates/ansible-inventory.j2",
        "templates/ssh-config.j2",
        "templates/ansible-vars.yml",
        "templates/vault-example.yml",
        "main.yml",
        "main_vms_only.yml",
        "acme_lab.setup.sh",
        "acme_lab.setup_vms_only.sh",
        "acme_lab.delete_all.sh",
        "acme_lab.delete_vms_only.sh",
        "acme_lab.reset.setup.sh",
    }
    assert required <= set(files)


def test_writer_never_emits_a_secrets_directory():
    # the installer creates secrets/ as a symlink and deletes any real dir first;
    # emitting one would be clobbered at best, a secret leak at worst.
    files = render_scenario(_spec())
    assert not any(p == "secrets" or p.startswith("secrets/") for p in files)


def test_writer_wires_tier_files_and_main_imports_agree():
    files = render_scenario(_spec())
    # the tier's stage files exist at the paths main.yml imports
    assert "02_admin_infrastructure/_main_stage_00.yml" in files
    assert "02_admin_infrastructure/_main_stage_01.yml" in files
    assert "02_admin_infrastructure/stage_00-vm_bootstrap/_r42_admin_group.yml" in files
    # every ./import in main.yml resolves to an emitted file
    main = yaml.safe_load(files["main.yml"])
    for entry in main:
        rel = entry["import_playbook"].lstrip("./")
        assert rel in files, f"main.yml imports {rel}, which the writer did not emit"


def test_writer_omits_stage_files_for_a_bundle_less_vm():
    # a VM with no bundles gets no configure file, and nothing must import one
    spec = ScenarioSpec(
        name="bare_lab",
        tiers=(
            TierSpec(
                key="ctf",
                number="04",
                group_id="r42_vuln_box_group",
                active_group="r42_ctf_active",
                vms=(
                    VmSpec(
                        vm_name="vuln-box-00",
                        vm_id=1170,
                        ip="192.168.144.170",
                        role="ctf",
                        bridge="vmbr144",
                        gateway="192.168.144.1",
                        template_vmid=9232,
                    ),
                ),
            ),
        ),
    )
    files = render_scenario(spec)
    assert "04_ctf_infrastructure/stage_01-vm_configure/vuln-box-00.yml" not in files
    stage01 = yaml.safe_load(files["04_ctf_infrastructure/_main_stage_01.yml"])
    imported = {e["import_playbook"].lstrip("./") for e in stage01}
    assert not any("vuln-box-00.yml" in p for p in imported)


def test_manifest_and_ssh_config_agree_on_vm_hostnames():
    files = render_scenario(_spec())
    manifest = yaml.safe_load(files["manifest/scenario_vms.json"])
    names = {vm["vm_name"] for vm in manifest["vms"]}
    ssh = files["templates/ssh-config.j2"]
    for name in names:
        assert f"Host r42.{name}" in ssh
