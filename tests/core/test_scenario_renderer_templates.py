"""Scenario renderer -- the per-workspace ``templates/`` scaffolding.

The deployer-cli's discovery gate (``SCENARIO_REQUIRED_FILES``) makes a scenario
invisible unless its ``templates/`` dir carries all four files. Three of them stay
``.j2`` because the installer renders them PER-WORKSPACE with variables the
scenario generator cannot know (``INFRASTRUCTURE_CODENAME``, the proxmox address,
the ssh-key destination dirs). So the renderer emits "concrete content wrapped in
a fixed Jinja frame", not a finished file -- and the tests below prove the frame
still renders and the concrete parts uphold the load-bearing invariants:

* every VM host is exactly ``r42.<vm_name>`` and shows up in BOTH the inventory
  and the ssh-config (the triple-agreement the manifest is the third leg of);
* inventory VM hosts carry NO ``ansible_host`` (their IP lives in ssh-config);
* the ``proxmox`` / ``proxmox_cli`` groups survive as literal Jinja;
* the only ``{{ }}`` left anywhere are the known workspace vars.
"""

from __future__ import annotations

import re

import jinja2
import yaml

from app.core.scenario_renderer.types import (
    BundleRef,
    CrossTierGroup,
    ScenarioSpec,
    TierSpec,
    VmSpec,
)
from app.core.scenario_renderer.workspace_templates import (
    render_ansible_vars,
    render_inventory_template,
    render_ssh_config_template,
    render_vault_example,
)

# The workspace vars the installer -- not the scenario generator -- substitutes.
# Anything else left as {{ }} in a rendered file is a bug.
ALLOWED_WORKSPACE_VARS = {
    "INFRASTRUCTURE_CODENAME",
    "INFRASTRUCTURE_PROXMOX_ADDRESS",
    "INFRASTRUCTURE_SCENARIO",
    "DEPLOYER_CLI__DST_SSH_KEYS_JUMP_DEST_DIR",
    "DEPLOYER_CLI__DST_SSH_KEYS_BACKEND_DEST_DIR",
}

_WORKSPACE_VALUES = {
    "INFRASTRUCTURE_CODENAME": "testcode",
    "INFRASTRUCTURE_PROXMOX_ADDRESS": "10.20.30.40",
    "INFRASTRUCTURE_SCENARIO": "demo_lab",
    "DEPLOYER_CLI__DST_SSH_KEYS_JUMP_DEST_DIR": "/keys/jump",
    "DEPLOYER_CLI__DST_SSH_KEYS_BACKEND_DEST_DIR": "/keys/backend",
}

_JINJA_VAR_RE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)")


def _jinja_vars(text: str) -> set[str]:
    """The set of top-level variable names referenced in ``{{ ... }}`` blocks."""
    return set(_JINJA_VAR_RE.findall(text))


def _render_workspace(text: str) -> str:
    """Render the fixed Jinja frame the way the installer would, with dummy vars."""
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    env.filters["mandatory"] = lambda v: v  # Ansible filter; identity is enough here
    return env.from_string(text).render(**_WORKSPACE_VALUES)


def _vm(vm_name: str, ip: str, **kw) -> VmSpec:
    defaults = dict(
        vm_id=1100,
        ip=ip,
        role="admin",
        bridge="vmbr142",
        gateway="192.168.142.1",
        template_vmid=9232,
    )
    defaults.update(kw)
    return VmSpec(vm_name=vm_name, **defaults)


def _spec(**kw) -> ScenarioSpec:
    admin = TierSpec(
        key="admin",
        number="02",
        group_id="r42_admin_group",
        active_group="r42_admin_active",
        vms=(
            _vm("admin-wazuh", "192.168.142.100", vm_id=1100),
            _vm("admin-misp", "192.168.142.112", vm_id=1101),
        ),
    )
    ctf = TierSpec(
        key="ctf",
        number="04",
        group_id="r42_ctf_group",
        active_group="r42_ctf_active",
        vms=(
            _vm("vuln-box-00", "192.168.144.170", vm_id=1200, role="ctf"),
            _vm("vuln-box-01", "192.168.144.171", vm_id=1201, role="ctf"),
        ),
    )
    defaults = dict(
        name="demo_lab",
        tiers=(admin, ctf),
        codename="cn",
        proxmox_address="10.20.30.40",
    )
    defaults.update(kw)
    return ScenarioSpec(**defaults)


def _inventory_host_names(rendered_yaml: str) -> set[str]:
    """Every leaf host key across all groups in a rendered inventory."""
    tree = yaml.safe_load(rendered_yaml)
    names: set[str] = set()

    def walk(node) -> None:
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            if key == "hosts" and isinstance(value, dict):
                names.update(value.keys())
            else:
                walk(value)

    walk(tree)
    return names


class TestInventoryTemplate:
    def test_renders_to_valid_yaml_after_workspace_substitution(self):
        rendered = _render_workspace(render_inventory_template(_spec()))

        tree = yaml.safe_load(rendered)

        assert isinstance(tree, dict)
        assert "all" in tree

    def test_vm_hosts_are_bare_with_no_ansible_host(self):
        # a VM's IP comes from ssh-config, never the inventory
        rendered = _render_workspace(render_inventory_template(_spec()))
        tree = yaml.safe_load(rendered)

        groups = tree["all"]["children"]["range42_infrastructure"]["children"]
        admin_hosts = groups["r42_admin"]["hosts"]

        assert set(admin_hosts) == {"r42.admin-wazuh", "r42.admin-misp"}
        # bare entry -> null mapping value; certainly no ansible_host
        assert all(v is None for v in admin_hosts.values())

    def test_carries_proxmox_and_proxmox_cli_groups_as_literal_jinja(self):
        # bundles run `- hosts: proxmox` and delegate to `<codename>-cli`
        raw = render_inventory_template(_spec())

        assert "proxmox:" in raw
        assert "proxmox_cli:" in raw
        assert "{{ INFRASTRUCTURE_CODENAME }}" in raw
        assert "{{ INFRASTRUCTURE_PROXMOX_ADDRESS | mandatory }}:8006" in raw

        groups = yaml.safe_load(_render_workspace(raw))["all"]["children"][
            "range42_infrastructure"
        ]["children"]
        assert groups["proxmox"]["hosts"]["testcode"]["ansible_connection"] == "local"
        assert "testcode-cli" in groups["proxmox_cli"]["hosts"]

    def test_tier_key_maps_to_the_reference_canonical_group_name(self):
        spec = ScenarioSpec(
            name="mixed",
            tiers=(
                TierSpec(
                    key="admin",
                    number="02",
                    group_id="x",
                    active_group="x",
                    vms=(_vm("admin-a", "192.168.142.10"),),
                ),
                TierSpec(
                    key="student",
                    number="03",
                    group_id="x",
                    active_group="x",
                    vms=(_vm("student-a", "192.168.143.10", role="student"),),
                ),
                TierSpec(
                    key="ctf",
                    number="04",
                    group_id="x",
                    active_group="x",
                    vms=(_vm("vuln-a", "192.168.144.10", role="ctf"),),
                ),
                TierSpec(
                    key="team",
                    number="05",
                    group_id="x",
                    active_group="x",
                    vms=(_vm("team-a", "192.168.145.10", role="team"),),
                ),
                TierSpec(
                    key="weird",
                    number="06",
                    group_id="x",
                    active_group="x",
                    vms=(_vm("weird-a", "192.168.146.10", role="weird"),),
                ),
            ),
        )

        groups = yaml.safe_load(_render_workspace(render_inventory_template(spec)))[
            "all"
        ]["children"]["range42_infrastructure"]["children"]

        assert "r42_admin" in groups
        assert "r42_student_box_group" in groups
        assert "r42_vuln_box_group" in groups
        assert "r42_blank_group" in groups
        assert "r42_weird_group" in groups  # derived fallback

    def test_finalize_cross_tier_group_is_emitted_with_its_members(self):
        admin = _spec().tiers[0]
        wazuh = CrossTierGroup(
            group_id="r42_demo_lab_wazuh_clients",
            group_var="wazuh_clients_group",
            bundle=BundleRef(name="admin/software.install.wazuh_agent"),
            members=admin.vms,
        )
        spec = _spec(finalize=(wazuh,))

        groups = yaml.safe_load(_render_workspace(render_inventory_template(spec)))[
            "all"
        ]["children"]["range42_infrastructure"]["children"]

        assert set(groups["r42_demo_lab_wazuh_clients"]["hosts"]) == {
            "r42.admin-wazuh",
            "r42.admin-misp",
        }

    def test_only_known_workspace_jinja_vars_remain(self):
        left = _jinja_vars(render_inventory_template(_spec()))

        assert left <= ALLOWED_WORKSPACE_VARS, f"unexpected jinja vars: {left}"

    def test_empty_tier_still_renders_loadable_yaml(self):
        spec = _spec(
            tiers=(
                TierSpec(
                    key="admin",
                    number="02",
                    group_id="r42_admin_group",
                    active_group="r42_admin_active",
                    vms=(),
                ),
            )
        )

        tree = yaml.safe_load(_render_workspace(render_inventory_template(spec)))

        assert "r42_admin" in tree["all"]["children"]["range42_infrastructure"][
            "children"
        ]


class TestSshConfigTemplate:
    def test_renders_a_host_and_hostname_block_for_every_vm(self):
        rendered = _render_workspace(render_ssh_config_template(_spec()))

        for vm in _spec().all_vms:
            assert f"Host {vm.ssh_name}\n" in rendered
            assert f"Hostname {vm.ip}" in rendered

    def test_keeps_the_fixed_header_frame_and_wildcard_identityfile(self):
        raw = render_ssh_config_template(_spec())

        # proxmox web-ui forward + root cli + jumper header must survive verbatim
        assert "PROXMOX WEB UI" in raw
        assert "{{ INFRASTRUCTURE_CODENAME }}-cli" in raw
        assert "Host px.{{ INFRASTRUCTURE_CODENAME }}.jumper" in raw
        # the per-VM wildcard carries the deployer key path as literal Jinja
        assert (
            "{{ DEPLOYER_CLI__DST_SSH_KEYS_BACKEND_DEST_DIR }}/r42."
            "{{ INFRASTRUCTURE_CODENAME }}-{{ INFRASTRUCTURE_SCENARIO }}"
            "-deployer-key_alice" in raw
        )

    def test_only_known_workspace_jinja_vars_remain(self):
        left = _jinja_vars(render_ssh_config_template(_spec()))

        assert left <= ALLOWED_WORKSPACE_VARS, f"unexpected jinja vars: {left}"


class TestTripleAgreement:
    def test_every_vm_host_matches_r42_dot_vm_name_in_both_files(self):
        spec = _spec()
        inv_hosts = _inventory_host_names(
            _render_workspace(render_inventory_template(spec))
        )
        ssh = _render_workspace(render_ssh_config_template(spec))

        for vm in spec.all_vms:
            assert vm.ssh_name == f"r42.{vm.vm_name}"
            assert vm.ssh_name in inv_hosts
            assert f"Host {vm.ssh_name}\n" in ssh


class TestAnsibleVars:
    def test_yaml_loads_and_pins_the_scenario_name(self):
        spec = _spec(name="forensics_lab")

        data = yaml.safe_load(render_ansible_vars(spec))

        assert data["INFRASTRUCTURE_SCENARIO"] == "forensics_lab"

    def test_carries_the_reference_credential_and_ci_user_keys(self):
        data = yaml.safe_load(render_ansible_vars(_spec()))

        assert data["context_auto_generate_ssh_keys"] == "YES"
        assert data["context_auto_generate_vm_passwords"] == "YES"
        assert data["default_admin_vm_ci_user"] == "alice"
        assert data["default_trainee_vm_ci_user"] == "bob"
        assert data["student_additionnal_keys_count"] == 5

    def test_has_no_leftover_jinja(self):
        assert _jinja_vars(render_ansible_vars(_spec())) == set()


class TestVaultExample:
    def test_yaml_loads_and_carries_the_vault_secret_keys(self):
        data = yaml.safe_load(render_vault_example(_spec()))

        assert data["proxmox_api_token_secret"] == "REPLACE_ME"
        assert data["jump_password"] == "REPLACE_ME"
        assert "default_admin_vm_ci_password" in data
        assert "default_trainee_vm_ci_password" in data
        assert "WAZUH_PASSWORD" in data

    def test_has_no_leftover_jinja(self):
        assert _jinja_vars(render_vault_example(_spec())) == set()
