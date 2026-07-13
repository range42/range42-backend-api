"""stage_01 -- per-VM software-attach rendering.

The stage_01 file installs software bundles onto one already-created VM. It is a
list of ``import_playbook`` blocks, each optionally flag-gated, each passing the
VM's identity (``global_vm_ssh_name`` / ``global_vm_ci_ip``) down to the bundle.
Reference shapes: demo_lab's ``admin-wazuh.yml`` (single gated bundle) and
``vuln_box_01.yml`` (several bundles stacked on one VM).

Only *VM-level* bundles belong here -- those whose ``main.yml`` declares
``hosts: "{{ global_vm_ssh_name }}"`` (every ``admin/*`` and ``ctf/*`` bundle).
The ``core/*`` bundles declare ``hosts: "{{ target_group }}"``: they are
group-level and belong to the tier baseline (``TierSpec.baseline_bundles``), not
to a per-VM file. These tests therefore only ever attach VM-level bundles.
"""

import os
import shutil
import subprocess
import textwrap

import pytest
import yaml

from app.core.scenario_renderer import BundleRef, VmSpec
from app.core.scenario_renderer.stage01 import render_vm_software


def test_single_bundle_renders_a_gated_import_carrying_the_vm_identity():
    # Mirrors demo_lab 02_admin_infrastructure/stage_01-vm_configure/admin-wazuh.yml
    vm = VmSpec(
        vm_name="admin-wazuh",
        vm_id=1100,
        ip="192.168.142.100",
        role="admin",
        bridge="vmbr142",
        gateway="192.168.142.1",
        template_vmid=9232,
        bundles=(BundleRef(name="admin/software.install.wazuh", install_flag="WAZUH"),),
    )

    plays = yaml.safe_load(render_vm_software(vm))

    assert isinstance(plays, list) and len(plays) == 1
    block = plays[0]
    assert block["import_playbook"].endswith(
        "range42-playbooks/bundles/admin/software.install.wazuh/main.yml"
    )
    assert block["when"] == 'INSTALL_WAZUH | default("NO") | upper == "YES"'
    # exactly the two vars the shipped admin bundles consume, nothing else
    assert block["vars"] == {
        "global_vm_ssh_name": "r42.admin-wazuh",
        "global_vm_ci_ip": "192.168.142.100",
    }


def _admin_box(*bundles: BundleRef) -> VmSpec:
    """One admin VM, used for the multi-bundle / merge / collision cases."""
    return VmSpec(
        vm_name="admin-services",
        vm_id=1110,
        ip="192.168.142.110",
        role="admin",
        bridge="vmbr142",
        gateway="192.168.142.1",
        template_vmid=9232,
        bundles=bundles,
    )


def test_bundles_stack_onto_one_vm_in_declared_order():
    # The vuln_box_01.yml shape: several bundles on a single VM. Install order is
    # meaningful -- declared order is run order, so it must survive rendering.
    vm = _admin_box(
        BundleRef(name="admin/software.install.gitea", install_flag="GITEA"),
        BundleRef(name="admin/software.install.mattermost", install_flag="MATTERMOST"),
        BundleRef(name="admin/software.install.nextcloud", install_flag="NEXTCLOUD"),
    )

    plays = yaml.safe_load(render_vm_software(vm))

    assert [b["import_playbook"].rsplit("/bundles/", 1)[1] for b in plays] == [
        "admin/software.install.gitea/main.yml",
        "admin/software.install.mattermost/main.yml",
        "admin/software.install.nextcloud/main.yml",
    ]
    # every block targets the same VM
    for block in plays:
        assert block["vars"]["global_vm_ssh_name"] == "r42.admin-services"
        assert block["vars"]["global_vm_ci_ip"] == "192.168.142.110"


def test_ungated_bundle_renders_no_when_key_at_all():
    # install_flag=None means "always install" -- an absent guard, not `when: true`.
    plays = yaml.safe_load(render_vm_software(_admin_box(BundleRef(name="admin/software.install.kong"))))

    assert "when" not in plays[0]


def test_gate_preserves_the_bundles_own_install_default():
    # demo_lab gates wazuh at YES and gitea at NO from the same renderer.
    vm = _admin_box(
        BundleRef(name="admin/software.install.wazuh", install_flag="WAZUH", install_default="YES"),
        BundleRef(name="admin/software.install.gitea", install_flag="GITEA", install_default="NO"),
    )

    plays = yaml.safe_load(render_vm_software(vm))

    assert plays[0]["when"] == 'INSTALL_WAZUH | default("YES") | upper == "YES"'
    assert plays[1]["when"] == 'INSTALL_GITEA | default("NO") | upper == "YES"'


def test_per_attachment_vars_are_merged_alongside_the_vm_identity():
    # BundleRef.vars is the declared extension point for tuning one attachment.
    # No shipped admin bundle consumes extras yet (they read only ssh_name/ci_ip),
    # so these names are illustrative -- what is under test is the merge itself.
    vm = _admin_box(
        BundleRef(
            name="admin/software.install.gitea",
            vars={"global_gitea_admin_user": "r42admin", "global_gitea_http_port": "3000"},
        )
    )

    block = yaml.safe_load(render_vm_software(vm))[0]

    assert block["vars"] == {
        "global_vm_ssh_name": "r42.admin-services",
        "global_vm_ci_ip": "192.168.142.110",
        "global_gitea_admin_user": "r42admin",
        "global_gitea_http_port": "3000",
    }


@pytest.mark.parametrize("owned", ["global_vm_ssh_name", "global_vm_ci_ip"])
def test_bundle_vars_may_not_hijack_the_vm_identity(owned):
    # The VM owns its identity; a bundle is a verb and owns none. A bundle var
    # that retargets ssh_name/ci_ip would silently install onto ANOTHER host and
    # still render as plausible YAML -- so it is a render-time error, not a merge.
    vm = _admin_box(BundleRef(name="admin/software.install.gitea", vars={owned: "r42.somewhere-else"}))

    with pytest.raises(ValueError, match=owned):
        render_vm_software(vm)


def test_vm_with_no_bundles_renders_nothing_so_the_caller_can_skip_the_file():
    # Verified against ansible-core 2.19: BOTH an empty file ("Empty playbook,
    # nothing to do") and a "[]" document ("A playbook must contain at least one
    # play") are FATAL (rc=4) when imported. There is no empty playbook Ansible
    # tolerates -- so emptiness must be signalled to the caller, not rendered.
    # An empty string is falsy: the caller writes no file and emits no import.
    # "[]\n" would be truthy, look writable, and blow up the deploy.
    assert render_vm_software(_admin_box()) == ""


def test_unknown_bundle_fails_the_render_rather_than_emitting_a_dangling_import():
    with pytest.raises(KeyError, match="admin/software.install.nope"):
        render_vm_software(_admin_box(BundleRef(name="admin/software.install.nope")))


# --------------------------------------------------------------------------
# DEFECT 1 -- the VM's install gate must be inherited by its bundle imports.
# A software bundle targets `hosts: r42.<vm_name>` BY NAME, so if the VM was
# gated off (stage_00 never created it) and the bundle import omits the VM's
# gate, the play fires against a host that does not exist -> UNREACHABLE, and
# the whole deploy aborts. The emitted guard must therefore AND the VM's gate
# with the bundle's own gate.
# --------------------------------------------------------------------------


def _gated_vm(*bundles: BundleRef, flag: str | None, default: str = "NO") -> VmSpec:
    """An admin VM that may itself be flag-gated (stage_00 may never create it)."""
    return VmSpec(
        vm_name="admin-deployer-ui",
        vm_id=1120,
        ip="192.168.142.120",
        role="admin",
        bridge="vmbr142",
        gateway="192.168.142.1",
        template_vmid=9232,
        install_flag=flag,
        install_default=default,
        bundles=bundles,
    )


def test_vm_gate_and_bundle_gate_are_combined_with_and():
    # Both gates present: a bundle on a gated-off VM must never fire, so the
    # guard is the conjunction -- VM gate first, then the bundle's own gate.
    vm = _gated_vm(
        BundleRef(name="admin/software.install.deployer-ui", install_flag="DEPLOYER_UI", install_default="NO"),
        flag="DEPLOYER_UI",
        default="NO",
    )

    block = yaml.safe_load(render_vm_software(vm))[0]

    assert block["when"] == (
        'INSTALL_DEPLOYER_UI | default("NO") | upper == "YES" '
        'and INSTALL_DEPLOYER_UI | default("NO") | upper == "YES"'
    )


def test_vm_gate_alone_guards_an_ungated_bundle():
    # The bundle is always-install, but the VM itself is gated: the import must
    # still carry the VM gate, or it targets a host that may never exist.
    vm = _gated_vm(
        BundleRef(name="admin/software.install.kong"),  # no install_flag
        flag="DEPLOYER_UI",
        default="NO",
    )

    block = yaml.safe_load(render_vm_software(vm))[0]

    assert block["when"] == 'INSTALL_DEPLOYER_UI | default("NO") | upper == "YES"'


def test_bundle_gate_alone_when_the_vm_is_ungated():
    # VM has no gate (always created) but the bundle is gated -> only the bundle
    # gate is emitted. This is the demo_lab admin-wazuh.yml shape.
    vm = _admin_box(BundleRef(name="admin/software.install.wazuh", install_flag="WAZUH", install_default="YES"))

    block = yaml.safe_load(render_vm_software(vm))[0]

    assert block["when"] == 'INSTALL_WAZUH | default("YES") | upper == "YES"'


def test_neither_gate_emits_no_when():
    vm = _gated_vm(BundleRef(name="admin/software.install.kong"), flag=None)

    assert "when" not in yaml.safe_load(render_vm_software(vm))[0]


# --------------------------------------------------------------------------
# DEFECT 2 -- only VM-level bundles may attach to a VM. A GROUP bundle needs
# `target_group` and an XTIER bundle needs a cross-tier group var; handed a
# VM's `global_vm_ssh_name` they die at deploy with `'target_group' is
# undefined`. Reject them at render time, naming the bundle and its kind.
# --------------------------------------------------------------------------


def test_group_bundle_attached_to_a_vm_is_rejected():
    vm = _admin_box(BundleRef(name="core/system.baseline.default"))  # GROUP kind

    with pytest.raises(ValueError, match=r"core/system\.baseline\.default.*group"):
        render_vm_software(vm)


def test_xtier_bundle_attached_to_a_vm_is_rejected():
    vm = _admin_box(BundleRef(name="admin/software.install.wazuh-agent"))  # XTIER kind

    with pytest.raises(ValueError, match=r"admin/software\.install\.wazuh-agent.*xtier"):
        render_vm_software(vm)


# --------------------------------------------------------------------------
# DEFECT 3 -- CTF/service ports are never opened. Bundles do not open their
# own firewall ports (the CTF ones carry a TODO saying so), so the VM's
# stage_01 file must PREPEND an aggregated firewall play opening port 22 plus
# the sorted union of every attached bundle's ports. Mirrors vuln_box_01.yml.
# --------------------------------------------------------------------------


def _ctf_box(*bundles: BundleRef, flag: str | None = None) -> VmSpec:
    return VmSpec(
        vm_name="vuln-box-01",
        vm_id=1400,
        ip="192.168.144.10",
        role="ctf",
        bridge="vmbr144",
        gateway="192.168.144.1",
        template_vmid=9232,
        install_flag=flag,
        bundles=bundles,
    )


def test_ported_bundles_prepend_an_aggregated_firewall_play():
    vm = _ctf_box(
        BundleRef(name="ctf/cve/network/openssh/CVE-2018-15473", ports=(2218,)),
        BundleRef(name="ctf/cve/crypto/openssl/CVE-2014-0160", ports=(8443,)),
    )

    plays = yaml.safe_load(render_vm_software(vm))

    fw = plays[0]
    assert fw["hosts"] == "r42.vuln-box-01"
    assert fw["become"] is True
    assert fw["roles"] == ["software.configure.firewalls"]
    # port 22 always open, then the sorted union of the bundles' ports
    assert fw["vars"]["firewall_rules"] == [
        {"ip": "all", "port": 22, "protocol": "tcp"},
        {"ip": "all", "port": 2218, "protocol": "tcp"},
        {"ip": "all", "port": 8443, "protocol": "tcp"},
    ]
    # the import blocks follow the firewall play, unchanged
    assert [p["import_playbook"].rsplit("/bundles/", 1)[1] for p in plays[1:]] == [
        "ctf/cve/network/openssh/CVE-2018-15473/main.yml",
        "ctf/cve/crypto/openssl/CVE-2014-0160/main.yml",
    ]


def test_firewall_ports_are_deduped_and_sorted_across_bundles():
    vm = _ctf_box(
        BundleRef(name="ctf/cve/network/openssh/CVE-2024-6387", ports=(2224, 22)),
        BundleRef(name="ctf/cve/network/openssh/CVE-2018-15473", ports=(2218, 2224)),
    )

    fw = yaml.safe_load(render_vm_software(vm))[0]

    assert [r["port"] for r in fw["vars"]["firewall_rules"]] == [22, 2218, 2224]


def test_no_ported_bundle_emits_no_firewall_play():
    # Unchanged behaviour: a VM whose bundles declare no ports renders only its
    # import blocks, no leading firewall play.
    vm = _ctf_box(BundleRef(name="admin/software.install.wazuh", install_flag="WAZUH"))

    plays = yaml.safe_load(render_vm_software(vm))

    assert all("import_playbook" in p for p in plays)


def test_gated_vm_firewall_play_carries_the_vm_gate_at_role_level():
    # A play cannot take a top-level `when` (invalid), and the firewall play
    # targets the VM by name -- so on a gated VM the gate rides the role entry,
    # and gather_facts is off so a gated-off host is never even connected to.
    vm = _ctf_box(
        BundleRef(name="ctf/cve/network/openssh/CVE-2018-15473", ports=(2218,)),
        flag="VULNBOX",
    )

    fw = yaml.safe_load(render_vm_software(vm))[0]

    assert fw["gather_facts"] is False
    assert fw["roles"] == [
        {"role": "software.configure.firewalls", "when": 'INSTALL_VULNBOX | default("YES") | upper == "YES"'}
    ]


# --------------------------------------------------------------------------
# End-to-end proof: the rendered file -- combined `when:` AND the firewall
# play -- parses under the real origin/dev bundles via ansible-playbook
# --syntax-check. Skips cleanly where the toolchain/repo is unavailable.
# --------------------------------------------------------------------------

_PLAYBOOKS_REPO = "/home/ppa/projects/range42-base/range42-playbooks"


def _archive_bundles(root: str) -> None:
    """Extract origin/dev's bundles/ into <root>/range42-playbooks/bundles."""
    archive = subprocess.run(
        ["git", "-C", _PLAYBOOKS_REPO, "archive", "origin/dev", "bundles"],
        check=True,
        capture_output=True,
    ).stdout
    dest = os.path.join(root, "range42-playbooks")
    os.makedirs(dest, exist_ok=True)
    subprocess.run(["tar", "-x", "-C", dest], input=archive, check=True)


@pytest.mark.skipif(
    shutil.which("ansible-playbook") is None or not os.path.isdir(os.path.join(_PLAYBOOKS_REPO, ".git")),
    reason="needs ansible-playbook and the range42-playbooks repo",
)
def test_rendered_stage01_passes_ansible_syntax_check(tmp_path):
    root = str(tmp_path)
    _archive_bundles(root)

    vm = _ctf_box(
        BundleRef(
            name="ctf/cve/network/openssh/CVE-2018-15473",
            install_flag="OPENSSH1",
            install_default="NO",
            ports=(2218,),
        ),
        BundleRef(name="ctf/cve/crypto/openssl/CVE-2014-0160", ports=(8443,)),
        flag="VULNBOX",
    )
    rendered = render_vm_software(vm)
    play_file = tmp_path / "vuln-box-01.yml"
    play_file.write_text(rendered)

    inventory = tmp_path / "hosts.yml"
    inventory.write_text(
        textwrap.dedent(
            """\
            all:
              hosts:
                r42.vuln-box-01: {ansible_host: 127.0.0.1}
            """
        )
    )

    # Stub the roles the imported bundles reference so syntax-check can resolve
    # them; we are proving the RENDERED file parses, not deploying the roles.
    roles_dir = tmp_path / "stubroles"
    for role in (
        "software.configure.firewalls",
        "software.configure.docker-compose",
        "software.install.warmup.basic_packages",
    ):
        tasks = roles_dir / role / "tasks"
        tasks.mkdir(parents=True)
        (tasks / "main.yml").write_text("- ansible.builtin.debug: {msg: stub}\n")

    cfg = tmp_path / "cfg"
    (cfg / "secrets").mkdir(parents=True)
    (cfg / "secrets" / "default_vault.yml").write_text("{}\n")

    env = {
        **os.environ,
        "RANGE42_GITDIR__ROOT_DIR": root,
        "RANGE42_ACTIVE_CONFIG_DIR": str(cfg),
        "RANGE42_INVENTORY": str(tmp_path / "inv"),
        "RANGE42_INVENTORY__DOCKER__CTF": str(tmp_path / "inv" / "ctf"),
        "ANSIBLE_ROLES_PATH": str(roles_dir),
    }
    result = subprocess.run(
        ["ansible-playbook", "-i", str(inventory), "--syntax-check", str(play_file)],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, (
        f"syntax-check failed:\n{result.stdout}\n{result.stderr}\n---rendered---\n{rendered}"
    )
