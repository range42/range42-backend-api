"""Application-owned compatibility for unchanged, reviewed native SDN sources.

The installation profile still verifies all runtime dependencies. These content
digests identify the contract of the selected bundle tree and the first role in
Ansible's search path; a revision label alone cannot authorize a different tree.
"""
import os
from pathlib import Path

from app.core.bundle_runtime import tree_digest

NATIVE_CONTRACT = "native-sdn-20260921"
# range42-playbooks 6dcf31b5b53600f41be1ce7553d489dba4ad803b / bundles
# proxmox_controller 617b57cddecbe4ddedd1f72becc7023bbcdf2114 / roles/<role>
REVIEWED_TREES = {
    ("56f8801125eeaa75737535ff1b9aae7d2d80741850b7381b7bc818e40cdfa0cb",
     "39d07c0541b24666a1396b9f8c6f9f5f50861d33060bd7e19bc0bf4d01530095"): NATIVE_CONTRACT,
}

# The native source-only sweep is safe for its simple, generated NAT rules.
# Refuse source-scoped LOG/ACCEPT, source negation, comments and extra predicates.
# Docker's negated egress MASQUERADE is counted and deleted literally by the
# native reader/sweep. Require one complete raw shape per source so its omitted
# egress-negation / SNAT-address metadata cannot conceal a mixed rule set.
# Source-free rows are untouched by the native sweep and its reader.
_OCTET = r"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])"
_IPV4 = rf"{_OCTET}(?:\.{_OCTET}){{3}}"
SUPPORTED_NATIVE_NAT_LINE = (
    rf"^(?:-P POSTROUTING ACCEPT|-A POSTROUTING (?!-s )(?!.* -s ).+"
    rf"|-A POSTROUTING -s {_IPV4}/(?:[0-9]|[12][0-9]|3[0-2]) (?:! )?-o [A-Za-z0-9_.:-]+ "
    rf"-j (?:MASQUERADE|SNAT --to-source {_IPV4}))$"
)


def native_contract(profile: dict) -> str | None:
    try:
        environment = profile["environment"]
        bundles = Path(environment["RANGE42_BUNDLE_DIR"])
        for directory in environment.get("ANSIBLE_ROLES_PATH", "").split(os.pathsep):
            if not directory:
                continue
            role = Path(directory) / "range42-ansible_roles-proxmox_controller"
            if role.exists():
                return REVIEWED_TREES.get((tree_digest(bundles), tree_digest(role)))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def snat_observation_play() -> dict:
    """Read after the composite and emit a bounded, identifiable completion record."""
    return {"name": "Observe native NAT after the requested operation", "hosts": "proxmox", "gather_facts": False,
            "tasks": [
                {"ansible.builtin.set_fact": {"network_list_snat_rules": []}},
                {"ansible.builtin.include_role": {"name": "range42-ansible_roles-proxmox_controller"},
                 "vars": {"proxmox_vm_action": "network_list_snat_rules"}},
                {"ansible.builtin.set_fact": {"r42_native_snat_observation": {
                    "complete": True, "node": "{{ proxmox_node }}", "rules": "{{ network_list_snat_rules }}"}}},
                {"ansible.builtin.debug": {"var": "r42_native_snat_observation"}},
            ]}


def snat_guard_play() -> dict:
    return {"name": "Verify native SNAT rule compatibility before mutation", "hosts": "proxmox", "gather_facts": False,
            "vars": {"r42_native_nat_line_pattern": SUPPORTED_NATIVE_NAT_LINE}, "tasks": [
                {"name": "Read the node's complete POSTROUTING chain", "ansible.builtin.command": {
                    "argv": ["iptables", "-t", "nat", "-S", "POSTROUTING"]},
                 "environment": {"PATH": "/usr/sbin:/sbin:/usr/bin:/bin"}, "delegate_to": "r42-proxmox-cli",
                 "register": "r42_native_nat_before", "changed_when": False, "no_log": True},
                {"name": "Refuse rule shapes outside the reviewed native NAT contract", "ansible.builtin.assert": {"that": [
                    "'-P POSTROUTING ACCEPT' in r42_native_nat_before.stdout_lines",
                    "(r42_native_nat_before.stdout_lines | length) <= 4096",
                    "(r42_native_nat_before.stdout_lines | reject('match', r42_native_nat_line_pattern) | list | length) == 0",
                ], "fail_msg": "Native NAT operations require simple source-scoped SNAT/MASQUERADE rules. This host has unsupported rules; review its NAT configuration before retrying."}},
                {"name": "Refuse different raw NAT shapes for one source", "ansible.builtin.assert": {"that": [
                    r"(r42_native_nat_shapes | length) == (r42_native_nat_shapes | map('regex_findall', '^-A POSTROUTING -s (\S+) ') | map('first') | unique | length)",
                ], "fail_msg": "One source has different NAT rules. Review egress predicates and translated addresses before applying SDN."},
                 "vars": {"r42_native_nat_shapes": "{{ r42_native_nat_before.stdout_lines | select('match', '^-A POSTROUTING -s ') | unique | list }}"}},
            ]}


def native_snat_count(observation: dict, subnet: str, node: str) -> int | None:
    if not isinstance(observation, dict) or observation.get("complete") is not True or observation.get("node") != node:
        return None
    rows = observation.get("rules")
    if not isinstance(rows, list) or len(rows) > 4096:
        return None
    count = 0
    for row in rows:
        if (not isinstance(row, dict) or row.get("proxmox_node") != node
                or row.get("snat_host") != "r42-proxmox-cli" or row.get("snat_target") not in ("SNAT", "MASQUERADE")
                or not isinstance(row.get("snat_source"), str) or not isinstance(row.get("snat_out_iface"), str)
                or type(row.get("snat_count")) is not int or not 0 <= row["snat_count"] < 2**31):
            return None
        if row["snat_source"] == subnet:
            count += row["snat_count"]
    return count if count < 2**31 else None
