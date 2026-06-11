"""Generate Ansible inventory from a topology document.

The backend is the single source of truth for inventory; the universal
playbook reads the static hosts.yml this module writes (it does NOT use
add_host at runtime).

The topology shape uses a unified ``nodes[]`` array discriminated by
``kind``. This writer extracts vm/lxc/docker nodes as inventory hosts.
Network/router/firewall/skin/group nodes are NOT hosts (they're handled by
the universal playbook's Proxmox-side provisioning, not Ansible-targeted
operations).

Group naming matches existing range42 conventions:
- ``r42_admin`` / ``r42_admin_wazuh_clients``  (role: admin)
- ``r42_blank_group``                          (role: team / trainee / shared)
- ``proxmox`` / ``proxmox-cli``                (synthesized from target host)

Pure: takes inputs, writes one file at ``dest``, returns the path. No other
side effects — safe to call from ``start_attempt()`` (T8).
"""
from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Any

import yaml

# Reuse the canonical per-team template renderer so inventory CIDR resolution
# never drifts from the playbook/compose expansion (see #84).
from app.overlay.expand_replication import _render_template


# Node kinds that become Ansible hosts (others are infra primitives)
_HOST_KINDS = {"vm", "lxc", "docker"}

# Maps topology-node `role` -> primary Ansible group
_ROLE_TO_GROUP = {
    "admin": "r42_admin",
    "team": "r42_blank_group",
    "trainee": "r42_blank_group",
    "shared": "r42_blank_group",
}

# Default ssh user per role (overridable by topology.naming.ssh_user_for_role)
_DEFAULT_SSH_USER = {
    "admin": "alice",
    "team": "bob",
    "trainee": "bob",
    "shared": "bob",
}


def _ssh_key_path(ssh_keys_dir: Path, role: str, user: str) -> str:
    """Match existing key naming. Admin uses admin_keys/, team/trainee uses student_keys/."""
    sub = "admin_keys" if role == "admin" else "student_keys"
    return str(ssh_keys_dir / sub / f"r42.deployer-key_{user}")


def _node_name(node: dict) -> str:
    """Topology nodes use ``id`` as the canonical identifier (schema field)."""
    return node.get("id") or node.get("name") or ""


def _expand_per_team(
    items: list[dict], team_count: int
) -> list[tuple[int | None, int, dict]]:
    """Yield (team_id, seq_within_scope, item) tuples.

    ``team_id`` is None for shared scope. ``seq`` is the index within the items
    list of that scope (used for IP allocation). Stable ordering: shared first
    (sorted by id), then per-team grouped by team_id then id.
    """
    pairs: list[tuple[int | None, int, dict]] = []
    shared = sorted(
        [i for i in items if i.get("replication", {}).get("scope") == "shared"],
        key=_node_name,
    )
    per_team = sorted(
        [i for i in items if i.get("replication", {}).get("scope") == "per_team"],
        key=_node_name,
    )
    for seq, item in enumerate(shared):
        pairs.append((None, seq, item))
    for team_id in range(1, team_count + 1):
        for seq, item in enumerate(per_team):
            pairs.append((team_id, seq, item))
    return pairs


def _hostname(prefix: str, team_id: int | None, name: str) -> str:
    if team_id is None:
        return f"r42.{prefix}-{name}"
    return f"r42.{prefix}-{team_id}-{name}"


def _ip_for_node(bridge_base: int, team_id: int | None, seq: int) -> str:
    octet = bridge_base + (team_id or 0)
    return f"192.168.{octet}.{200 + seq}"


def _resolve_network_cidr(
    net_node: dict, team_id: int | None, bridge_base: int
) -> str | None:
    """Resolve a network node's CIDR, rendering a node-level ``cidr_template``
    (canonical schema form) for the given team if needed."""
    if net_node.get("cidr"):
        return net_node["cidr"]
    tpl = net_node.get("cidr_template")
    if tpl:
        return _render_template(tpl, team_id or 0, bridge_base)
    return None


def _host_ip_from_cidr(cidr: str, seq: int) -> str | None:
    """Derive a host address inside ``cidr`` following the existing host-octet
    convention (network address + 200 + seq). Returns None on a malformed CIDR."""
    try:
        net = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return None
    return str(net.network_address + (200 + seq))


def _resolve_network(
    net_node: dict, team_id: int | None, bridge_base: int
) -> dict[str, str | None]:
    """Resolve a network node's ``{bridge, cidr, gateway}`` for a team,
    rendering node-level templates via the canonical renderer."""
    tid = team_id or 0
    bridge = net_node.get("bridge")
    if not bridge and net_node.get("bridge_template"):
        bridge = _render_template(net_node["bridge_template"], tid, bridge_base)
    gateway = net_node.get("gateway")
    if not gateway and net_node.get("gateway_template"):
        gateway = _render_template(net_node["gateway_template"], tid, bridge_base)
    return {
        "bridge": bridge,
        "cidr": _resolve_network_cidr(net_node, team_id, bridge_base),
        "gateway": gateway,
    }


def _host_ci_net(
    node: dict, team_id: int | None, bridge_base: int,
    networks_by_id: dict[str, dict],
) -> tuple[int, str, str]:
    """Per-host cloud-init network triple (netmask, gateway, bridge). Derived
    from the bound network, else the legacy 192.168.{bridge_base+team} scheme."""
    octet = bridge_base + (team_id or 0)
    netmask, gateway, bridge = 24, f"192.168.{octet}.1", f"vmbr{octet}"
    nics = node.get("networks") or []
    primary = nics[0] if nics else None
    ref = primary.get("node_ref") if primary else None
    if ref and ref in networks_by_id:
        r = _resolve_network(networks_by_id[ref], team_id, bridge_base)
        if r["cidr"] and "/" in r["cidr"]:
            try:
                netmask = int(r["cidr"].split("/")[1])
            except ValueError:
                pass
        if r["gateway"]:
            gateway = r["gateway"]
        if r["bridge"]:
            bridge = r["bridge"]
    return netmask, gateway, bridge


def _build_network_map(
    topology: dict, team_count: int, bridge_base: int
) -> dict[str, dict[str, dict]]:
    """Per network id, the resolved {bridge, cidr, gateway} keyed by team id
    (or 'shared') — the single source the playbook reads to create bridges."""
    out: dict[str, dict[str, dict]] = {}
    for net in topology.get("nodes", []):
        if net.get("kind") != "network" or not net.get("id"):
            continue
        scope = (net.get("replication") or {}).get("scope", "shared")
        entry: dict[str, dict] = {}
        if scope == "per_team":
            for tid in range(1, team_count + 1):
                entry[str(tid)] = _resolve_network(net, tid, bridge_base)
        else:
            entry["shared"] = _resolve_network(net, None, bridge_base)
        out[net["id"]] = entry
    return out


def _ansible_host_ip(
    node: dict, team_id: int | None, seq: int, bridge_base: int,
    networks_by_id: dict[str, dict],
) -> str:
    """Determine a host's ansible_host. Precedence: explicit NIC ip >
    node_ref-bound network CIDR derivation > hardcoded 192.168.{bridge_base}
    fallback (backward-compatible with topologies that omit networks[])."""
    nics = node.get("networks") or []
    primary = nics[0] if nics else None
    if primary:
        explicit = primary.get("ip")
        if explicit:
            return explicit
        ref = primary.get("node_ref")
        net = networks_by_id.get(ref) if ref else None
        if net is not None:
            cidr = _resolve_network_cidr(net, team_id, bridge_base)
            if cidr:
                derived = _host_ip_from_cidr(cidr, seq)
                if derived:
                    return derived
    return _ip_for_node(bridge_base, team_id, seq)


def write_inventory(
    *,
    topology: dict[str, Any],
    team_count: int,
    codename: str,
    proxmox_address: str,
    ssh_keys_dir: Path,
    dest: Path,
) -> Path:
    """Render ``topology`` to a static Ansible inventory at ``dest``.

    Returns ``dest``.

    Args:
        topology: Parsed topology document (CatalogEntry of kind=gamenet).
        team_count: Number of teams to multiplex per_team-scoped nodes across.
        codename: Workspace codename (used for proxmox group host names).
        proxmox_address: Proxmox API address (host or IP).
        ssh_keys_dir: Directory containing admin_keys/ and student_keys/.
        dest: Output path for the rendered hosts.yml.

    Raises:
        ValueError: If a host-kind node is missing the required ``role`` field.
    """
    prefix = topology.get("naming_prefix", codename.lower())
    bridge_base = topology.get("bridge_base", 140)
    ssh_user_for_role = topology.get("naming", {}).get("ssh_user_for_role", {})

    # Filter nodes: only vm/lxc/docker become Ansible hosts
    host_nodes = [n for n in topology.get("nodes", []) if n.get("kind") in _HOST_KINDS]

    # Network nodes, keyed by id, for NIC node_ref -> CIDR resolution.
    networks_by_id = {
        n.get("id"): n
        for n in topology.get("nodes", [])
        if n.get("kind") == "network" and n.get("id")
    }

    children: dict[str, dict[str, Any]] = {
        "r42_admin": {"hosts": {}},
        "r42_admin_wazuh_clients": {"hosts": {}},
        "r42_blank_group": {"hosts": {}},
        "proxmox": {
            "hosts": {
                codename.lower(): {"ansible_host": f"{proxmox_address}:8006"}
            }
        },
        "proxmox-cli": {
            "hosts": {
                f"{codename.lower()}-cli": {
                    "ansible_host": proxmox_address,
                    "ansible_connection": "paramiko_ssh",
                    "ansible_user": "root",
                }
            }
        },
    }

    for team_id, seq, node in _expand_per_team(host_nodes, team_count):
        role = node.get("role")
        node_id = _node_name(node)
        if not role:
            raise ValueError(
                f"Node '{node_id}' (kind={node.get('kind')}) missing 'role' "
                f"(required by inventory_writer)"
            )
        group = _ROLE_TO_GROUP.get(role, "r42_blank_group")

        host = _hostname(prefix, team_id, node_id)
        user = ssh_user_for_role.get(role, _DEFAULT_SSH_USER.get(role, "alice"))
        ip = _ansible_host_ip(node, team_id, seq, bridge_base, networks_by_id)
        ci_netmask, ci_gateway, net_bridge = _host_ci_net(
            node, team_id, bridge_base, networks_by_id
        )

        children[group]["hosts"][host] = {
            "ansible_host": ip,
            "ansible_user": user,
            "ansible_ssh_private_key_file": _ssh_key_path(ssh_keys_dir, role, user),
            "ansible_ssh_common_args": (
                f"-o StrictHostKeyChecking=accept-new "
                f"-o ProxyJump=root@{proxmox_address}"
            ),
            "r42_team_id": team_id,
            "r42_node_name": node_id,
            "r42_template_vmid": node.get("template_vmid"),
            # Cloud-init network triple — single source for the playbook (#73).
            "r42_ci_netmask": ci_netmask,
            "r42_ci_gateway": ci_gateway,
            "r42_net_bridge": net_bridge,
        }

        # If this node has a wazuh-agent attachment, also list under
        # r42_admin_wazuh_clients (membership-only entry, no host vars).
        for att in node.get("attachments", []):
            source = att.get("source") or {}
            ref = source.get("ref", "") if isinstance(source, dict) else ""
            if not ref:
                ref = att.get("ref", "")
            if "wazuh" in ref.lower() and "agent" in ref.lower():
                children["r42_admin_wazuh_clients"]["hosts"][host] = {}

    network_map = _build_network_map(topology, team_count, bridge_base)
    inv = {"all": {"vars": {"r42_network_map": network_map}, "children": children}}
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(yaml.safe_dump(inv, sort_keys=False))
    return dest
