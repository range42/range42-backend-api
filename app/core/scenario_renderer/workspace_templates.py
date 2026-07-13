"""Scenario renderer -- the per-workspace ``templates/`` scaffolding.

The deployer-cli only *discovers* a scenario when its ``templates/`` dir carries
all four of ``ansible-inventory.j2``, ``ssh-config.j2``, ``ansible-vars.yml`` and
``vault-example.yml`` (its ``SCENARIO_REQUIRED_FILES`` gate). Three of them stay
``.j2`` because the installer renders them PER-WORKSPACE with variables no
scenario generator can know: the workspace codename, the proxmox address and the
ssh-key destination dirs. So each function here emits *concrete* content -- the
host groups and IPs resolved from the spec -- wrapped in a *fixed* Jinja frame
copied verbatim from the reference scenarios (``demo_lab``,
``blank_scenario_2_subnets``). We are not running a template engine; we are
stitching literal strings, and the only ``{{ }}`` left in our output are the
workspace vars the installer fills in later.

Load-bearing invariants (verified against the deployer-cli contract):

* a VM host is exactly ``r42.<vm_name>`` and appears in BOTH the inventory hosts
  and the ssh-config ``Host`` lines (the manifest is the third leg);
* inventory VM hosts are *bare* -- no ``ansible_host`` -- because their IP is
  resolved through ssh-config, not the inventory;
* the ``proxmox`` / ``proxmox_cli`` groups stay as literal Jinja because bundles
  run ``- hosts: proxmox`` and delegate to ``{{ codename }}-cli``.
"""

from __future__ import annotations

from app.core.scenario_renderer.types import ScenarioSpec, TierSpec, VmSpec

# --------------------------------------------------------------------------- #
# tier.key -> static inventory group name.
#
# The reference scenarios do NOT use one uniform pattern -- admin is bare
# ``r42_admin`` while student/ctf/team carry a ``_box_group`` / ``_group``
# suffix -- so we map the known keys to the exact reference names and derive
# ``r42_<key>_group`` for anything else.
# --------------------------------------------------------------------------- #
_TIER_GROUP_NAMES = {
    "admin": "r42_admin",
    "student": "r42_student_box_group",
    "ctf": "r42_vuln_box_group",
    "team": "r42_blank_group",
}


def _tier_group_name(key: str) -> str:
    return _TIER_GROUP_NAMES.get(key, f"r42_{key}_group")


# --------------------------------------------------------------------------- #
# ansible-inventory.j2
# --------------------------------------------------------------------------- #

# Literal Jinja tail: the two groups every scenario always carries. Bundles run
# `- hosts: proxmox` and delegate to `{{ codename }}-cli`, so these host names
# are the workspace's -- never the generator's -- and stay as {{ }}.
_INVENTORY_PROXMOX_GROUPS = """\
        proxmox:
          hosts:
            {{ INFRASTRUCTURE_CODENAME }}:
              ansible_host: {{ INFRASTRUCTURE_PROXMOX_ADDRESS | mandatory }}:8006
              ansible_connection: local
              ansible_python_interpreter: /usr/bin/python3

        proxmox_cli:
          hosts:
            {{ INFRASTRUCTURE_CODENAME }}-cli:
"""


def _inventory_group_block(name: str, vms: tuple[VmSpec, ...]) -> str:
    """One inventory group: bare ``r42.<vm_name>:`` hosts, no ``ansible_host``."""
    lines = ["        ####", "", f"        {name}:"]
    if vms:
        lines.append("          hosts:")
        lines.extend(f"            {vm.ssh_name}:" for vm in vms)
    else:
        # `hosts:` with no children loads as null and Ansible tolerates it, but
        # an explicit empty mapping keeps the group unambiguously host-less.
        lines.append("          hosts: {}")
    lines.append("")
    return "\n".join(lines)


def render_inventory_template(spec: ScenarioSpec) -> str:
    """Render ``templates/ansible-inventory.j2`` for the scenario."""
    parts = [
        "all:",
        "  children:",
        "    range42_infrastructure:",
        "      children:",
    ]
    for tier in spec.tiers:
        parts.append(_inventory_group_block(_tier_group_name(tier.key), tier.vms))
    for group in spec.finalize:
        parts.append(_inventory_group_block(group.group_id, group.members))
    parts.append("        ####")
    parts.append("")
    parts.append(_INVENTORY_PROXMOX_GROUPS)
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# ssh-config.j2
# --------------------------------------------------------------------------- #

_BANNER = "#### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####"

# Fixed header frame -- verbatim from the reference. All of its {{ }} are
# workspace vars the installer resolves per-workspace.
_SSH_HEADER = f"""\
{_BANNER}
#
#       infrastructure code name : {{{{ INFRASTRUCTURE_CODENAME }}}}
# infrastructure proxmox address : {{{{ INFRASTRUCTURE_PROXMOX_ADDRESS }}}}
#                       scenario : {{{{ INFRASTRUCTURE_SCENARIO }}}}
#
{_BANNER}

{_BANNER}
#                                      PROXMOX WEB UI (port forward)
{_BANNER}

Host px.{{{{ INFRASTRUCTURE_CODENAME }}}}.redirect_www.proxmox
    RequestTTY no
    RemoteCommand none
    localforward localhost:18042 {{{{ INFRASTRUCTURE_PROXMOX_ADDRESS | mandatory }}}}:8006
    localcommand sh -c '/usr/bin/chromium-browser --incognito --new-window --disable-translate  https://127.0.0.1:18042 &'

{_BANNER}
#                                      PX ROOT USER SSH ACCESS
{_BANNER}

# root SSH access to Proxmox -- for disk extend, pveum, etc.
# alias: CODENAME-cli matches the inventory group proxmox_cli
Host px.{{{{ INFRASTRUCTURE_CODENAME }}}}-ssh_cli.root {{{{ INFRASTRUCTURE_CODENAME }}}}-cli
    Hostname {{{{ INFRASTRUCTURE_PROXMOX_ADDRESS | mandatory }}}}
    User root
    IdentityFile {{{{ DEPLOYER_CLI__DST_SSH_KEYS_JUMP_DEST_DIR }}}}/px.{{{{ INFRASTRUCTURE_CODENAME }}}}-{{{{ INFRASTRUCTURE_SCENARIO }}}}-ssh_cli.root
    Port 22

{_BANNER}
#                                      SSH JUMPER
{_BANNER}

Host px.{{{{ INFRASTRUCTURE_CODENAME }}}}.jumper
    Hostname {{{{ INFRASTRUCTURE_PROXMOX_ADDRESS | mandatory }}}}
    User jump_user
    IdentityFile {{{{ DEPLOYER_CLI__DST_SSH_KEYS_JUMP_DEST_DIR }}}}/px.{{{{ INFRASTRUCTURE_CODENAME }}}}-{{{{ INFRASTRUCTURE_SCENARIO }}}}-ssh_cli.jump_user
    Port 22
"""

# Fixed Jinja wildcard trailer -- one block matching every VM host. The
# reference splits this per tier only because history did; a single ``r42.*``
# block is equivalent since every VM shares the same deployer key, user and
# jump. Its IdentityFile path stays literal Jinja.
_SSH_WILDCARD = """\
Host r42.*
    User alice
    IdentityFile {{ DEPLOYER_CLI__DST_SSH_KEYS_BACKEND_DEST_DIR }}/r42.{{ INFRASTRUCTURE_CODENAME }}-{{ INFRASTRUCTURE_SCENARIO }}-deployer-key_alice
    Port 22
    ProxyJump px.{{ INFRASTRUCTURE_CODENAME }}.jumper
"""


def _ssh_tier_section(tier: TierSpec) -> str:
    """A commented banner then one ``Host``/``Hostname`` block per VM in the tier."""
    lines = [
        _BANNER,
        f"#                                      {tier.key.upper()} VMs",
        _BANNER,
        "",
    ]
    for vm in tier.vms:
        lines.append(f"Host {vm.ssh_name}")
        lines.append(f"    Hostname {vm.ip}")
        lines.append("")
    return "\n".join(lines)


def render_ssh_config_template(spec: ScenarioSpec) -> str:
    """Render ``templates/ssh-config.j2`` for the scenario."""
    parts = [_SSH_HEADER]
    for tier in spec.tiers:
        if tier.vms:
            parts.append(_ssh_tier_section(tier))
    parts.append(_SSH_WILDCARD)
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# ansible-vars.yml  (static apart from the scenario name -- no Jinja)
# --------------------------------------------------------------------------- #


def render_ansible_vars(spec: ScenarioSpec) -> str:
    """Render ``templates/ansible-vars.yml`` -- pins ``INFRASTRUCTURE_SCENARIO``."""
    return f"""\
---
################################################################################
# range42 -- scenario-specific variables ({spec.name})
#
# These variables are specific to this scenario and may differ
# between scenarios running on the same CODENAME infrastructure.
#
# For shared variables -> group_vars/all/vars.yml
# For secrets -> vault.yml (see vault.yml.example)
#
################################################################################


#### SCENARIO IDENTIFICATION ####

INFRASTRUCTURE_SCENARIO: "{spec.name}"


#### CREDENTIAL GENERATION ####

# auto-generate ssh keys and passwords (YES/NO)
context_auto_generate_ssh_keys: "YES"
context_auto_generate_vm_passwords: "YES"

# number of additional student ssh keys (bob_1 .. bob_N)
student_additionnal_keys_count: 5


#### CLOUD-INIT USERNAMES ####

# admin vm user (not secret -- password and ssh key are in vault)
default_admin_vm_ci_user: "alice"

# student vm user
default_trainee_vm_ci_user: "bob"
"""


# --------------------------------------------------------------------------- #
# vault-example.yml  (static template of vault KEYS -- no per-scenario data)
# --------------------------------------------------------------------------- #


def render_vault_example(spec: ScenarioSpec) -> str:
    """Render ``templates/vault-example.yml`` -- the static vault-key template."""
    return f"""\
---
################################################################################
# range42 -- vault secrets ({spec.name})
#
# This file contains ALL secret values for this CODENAME-SCENARIO.
#
# Usage:
#   1. Copy this file: cp vault.yml.example vault.yml
#   2. Fill in real values
#   3. Encrypt: ansible-vault encrypt vault.yml
#   4. Never commit the unencrypted version
#
# Password format: use only a-z A-Z 0-9 - _ (20+ characters recommended)
#
################################################################################


#### PROXMOX API SECRET ####

# api token secret -- generated by proxmox.api-token or imported from existing
proxmox_api_token_secret: "REPLACE_ME"


#### JUMP HOST ####

# jump user password (initial password, used for user creation)
jump_password: "REPLACE_ME"


#### CLOUD-INIT PASSWORDS ####

# admin vm (alice) password
default_admin_vm_ci_password: "REPLACE_ME"

# admin vm (alice) ssh public key -- populated by credentials.generate
default_admin_vm_ci_ssh_key: "ssh-ed25519 AAAA... alice CODENAME-SCENARIO"

# student vm (bob) password
default_trainee_vm_ci_password: "REPLACE_ME"

# student vm (bob) ssh public key -- populated by credentials.generate
default_trainee_vm_ci_ssh_key: "ssh-ed25519 AAAA... bob CODENAME-SCENARIO"


#### TAILSCALE ####

# tailscale auth key (for joining the tailnet)
infrastructure_tailscale_authkey: "tskey-auth-REPLACE_ME"

# tailscale api key (for managing devices)
infrastructure_tailscale_apikey: "tskey-api-REPLACE_ME"


#### WAZUH ####

# wazuh admin password
# WAZUH_PASSWORD is consumed by credentials.vault template
# and mapped to infrastructure_wazuh_admin_password in the generated vault
WAZUH_PASSWORD: "REPLACE_ME"


#### MISC ####

# deployer-cli known_hosts path (not really secret, but stored in vault historically)
deployer_cli_user_ssh_known_hosts: "/home/your_deployer_cli_username/.ssh/known_hosts"
"""
