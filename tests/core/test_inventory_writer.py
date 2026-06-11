"""Tests for app.core.inventory_writer — topology→hosts.yml rendering."""
import json
from pathlib import Path

import pytest
import yaml

from app.core.inventory_writer import write_inventory


FIXTURES = Path(__file__).parent / "fixtures" / "topologies"


def test_writes_minimal_inventory(tmp_path):
    topology = json.loads((FIXTURES / "01-minimal.json").read_text())
    out = tmp_path / "hosts.yml"
    write_inventory(
        topology=topology,
        team_count=1,
        codename="MIN",
        proxmox_address="10.0.0.1",
        ssh_keys_dir=tmp_path / "ssh_keys",
        dest=out,
    )

    inv = yaml.safe_load(out.read_text())
    assert "all" in inv
    children = inv["all"]["children"]
    assert "r42_admin" in children
    assert "proxmox" in children
    # The minimal fixture has one admin node with id "host-01"
    assert any("host-01" in h for h in children["r42_admin"]["hosts"])


def test_writes_multi_team_inventory(tmp_path):
    topology = json.loads((FIXTURES / "02-multi-team.json").read_text())
    out = tmp_path / "hosts.yml"
    write_inventory(
        topology=topology,
        team_count=3,
        codename="MT",
        proxmox_address="10.0.0.1",
        ssh_keys_dir=tmp_path / "ssh_keys",
        dest=out,
    )

    inv = yaml.safe_load(out.read_text())
    children = inv["all"]["children"]

    # Shared admin VM appears once
    admin_hosts = list(children["r42_admin"]["hosts"].keys())
    assert len([h for h in admin_hosts if "wazuh" in h]) == 1

    # Per-team trainee VM appears 3 times
    blank_hosts = list(children["r42_blank_group"]["hosts"].keys())
    assert any("1-trainee" in h for h in blank_hosts)
    assert any("2-trainee" in h for h in blank_hosts)
    assert any("3-trainee" in h for h in blank_hosts)


def test_inventory_uses_correct_ip_for_team(tmp_path):
    topology = json.loads((FIXTURES / "02-multi-team.json").read_text())
    out = tmp_path / "hosts.yml"
    write_inventory(
        topology=topology,
        team_count=2,
        codename="MT",
        proxmox_address="10.0.0.1",
        ssh_keys_dir=tmp_path / "ssh_keys",
        dest=out,
    )

    inv = yaml.safe_load(out.read_text())
    blank = inv["all"]["children"]["r42_blank_group"]["hosts"]
    # bridge_base=140; team 1 -> 192.168.141.200; team 2 -> 192.168.142.200
    team1_host = next(h for h in blank if "1-trainee" in h)
    team2_host = next(h for h in blank if "2-trainee" in h)
    assert blank[team1_host]["ansible_host"] == "192.168.141.200"
    assert blank[team2_host]["ansible_host"] == "192.168.142.200"


def test_skips_non_host_node_kinds(tmp_path):
    """network/router/firewall/skin/group nodes are NOT inventory hosts.
    Only vm/lxc/docker nodes should appear in the host inventory."""
    topology = json.loads((FIXTURES / "02-multi-team.json").read_text())
    # Multi-team fixture has a 'network' node — shouldn't appear in any inventory group
    out = tmp_path / "hosts.yml"
    write_inventory(
        topology=topology,
        team_count=1,
        codename="MT",
        proxmox_address="10.0.0.1",
        ssh_keys_dir=tmp_path / "ssh_keys",
        dest=out,
    )
    inv = yaml.safe_load(out.read_text())
    all_hosts = []
    for grp in inv["all"]["children"].values():
        all_hosts.extend(grp.get("hosts", {}).keys())
    assert not any("team-net" in h for h in all_hosts), \
        "Network nodes must not appear as inventory hosts"


def _write(topology, tmp_path, **kw):
    out = tmp_path / "hosts.yml"
    write_inventory(
        topology=topology, codename="X", proxmox_address="10.0.0.1",
        ssh_keys_dir=tmp_path, dest=out, **kw,
    )
    return yaml.safe_load(out.read_text())


def test_ansible_host_derives_from_bound_static_network_cidr(tmp_path):
    """A host NIC's node_ref points to a network node with a static cidr;
    ansible_host is derived from that cidr (network addr + 200 + seq), not the
    hardcoded 192.168.{bridge_base} scheme."""
    topology = {
        "schema_version": "1.0", "kind": "gamenet", "bridge_base": 140,
        "nodes": [
            {"id": "net-custom", "kind": "network",
             "replication": {"scope": "shared"}, "cidr": "10.50.0.0/24"},
            {"id": "host-01", "kind": "vm", "role": "admin",
             "replication": {"scope": "shared"}, "template_vmid": 9001,
             "config": {}, "networks": [{"node_ref": "net-custom"}],
             "attachments": []},
        ],
    }
    inv = _write(topology, tmp_path, team_count=1)
    admin = inv["all"]["children"]["r42_admin"]["hosts"]
    host = next(h for h in admin if "host-01" in h)
    assert admin[host]["ansible_host"] == "10.50.0.200"


def test_explicit_nic_ip_overrides_network_derivation(tmp_path):
    """An explicit NIC ip takes precedence over node_ref cidr derivation."""
    topology = {
        "schema_version": "1.0", "kind": "gamenet", "bridge_base": 140,
        "nodes": [
            {"id": "net-custom", "kind": "network",
             "replication": {"scope": "shared"}, "cidr": "10.50.0.0/24"},
            {"id": "host-01", "kind": "vm", "role": "admin",
             "replication": {"scope": "shared"}, "template_vmid": 9001,
             "config": {}, "networks": [{"node_ref": "net-custom", "ip": "10.50.0.42"}],
             "attachments": []},
        ],
    }
    inv = _write(topology, tmp_path, team_count=1)
    admin = inv["all"]["children"]["r42_admin"]["hosts"]
    host = next(h for h in admin if "host-01" in h)
    assert admin[host]["ansible_host"] == "10.50.0.42"


def test_ansible_host_derives_from_per_team_cidr_template(tmp_path):
    """A per-team host bound to a per-team network with a node-level
    cidr_template resolves the cidr per team id and derives the host ip."""
    topology = {
        "schema_version": "1.0", "kind": "gamenet", "bridge_base": 140,
        "nodes": [
            {"id": "team-net", "kind": "network",
             "replication": {"scope": "per_team"},
             "cidr_template": "10.{{ bridge_base + team_id }}.0.0/16"},
            {"id": "box", "kind": "vm", "role": "team",
             "replication": {"scope": "per_team"}, "template_vmid": 9020,
             "config": {}, "networks": [{"node_ref": "team-net"}],
             "attachments": []},
        ],
    }
    inv = _write(topology, tmp_path, team_count=2)
    blank = inv["all"]["children"]["r42_blank_group"]["hosts"]
    t1 = next(h for h in blank if "1-box" in h)
    t2 = next(h for h in blank if "2-box" in h)
    # team1 -> 10.141.0.0/16 + 200 -> 10.141.0.200 ; team2 -> 10.142.0.200
    assert blank[t1]["ansible_host"] == "10.141.0.200"
    assert blank[t2]["ansible_host"] == "10.142.0.200"


def test_host_gets_cloudinit_network_vars_from_bound_network(tmp_path):
    """A host bound to a network exposes r42_ci_netmask/r42_ci_gateway/
    r42_net_bridge (resolved from that network) for the playbook's cloud-init —
    single source of truth, no playbook-side recompute."""
    topology = {
        "schema_version": "1.0", "kind": "gamenet", "bridge_base": 140,
        "nodes": [
            {"id": "net-a", "kind": "network", "replication": {"scope": "shared"},
             "cidr": "10.20.0.0/24", "gateway": "10.20.0.254", "bridge": "vmbr80"},
            {"id": "host-01", "kind": "vm", "role": "admin",
             "replication": {"scope": "shared"}, "template_vmid": 9001,
             "config": {}, "networks": [{"node_ref": "net-a"}], "attachments": []},
        ],
    }
    inv = _write(topology, tmp_path, team_count=1)
    admin = inv["all"]["children"]["r42_admin"]["hosts"]
    host = admin[next(h for h in admin if "host-01" in h)]
    assert host["ansible_host"] == "10.20.0.200"
    assert host["r42_ci_netmask"] == 24
    assert host["r42_ci_gateway"] == "10.20.0.254"
    assert host["r42_net_bridge"] == "vmbr80"


def test_inventory_exposes_network_map(tmp_path):
    """inventory carries all.vars.r42_network_map: per network id + team, the
    resolved {bridge, cidr, gateway} the playbook needs to create bridges."""
    topology = {
        "schema_version": "1.0", "kind": "gamenet", "bridge_base": 140,
        "nodes": [
            {"id": "team-net", "kind": "network", "replication": {"scope": "per_team"},
             "cidr_template": "10.{{ bridge_base + team_id }}.0.0/16",
             "bridge_template": "vmbr{{ bridge_base + team_id }}",
             "gateway_template": "10.{{ bridge_base + team_id }}.0.1"},
        ],
    }
    inv = _write(topology, tmp_path, team_count=2)
    nmap = inv["all"]["vars"]["r42_network_map"]
    assert nmap["team-net"]["1"] == {
        "bridge": "vmbr141", "cidr": "10.141.0.0/16", "gateway": "10.141.0.1"}
    assert nmap["team-net"]["2"] == {
        "bridge": "vmbr142", "cidr": "10.142.0.0/16", "gateway": "10.142.0.1"}


def test_host_without_network_keeps_legacy_cloudinit_vars(tmp_path):
    """Backward-compat: a host with no networks[] falls back to the existing
    192.168.{bridge_base+team} scheme for ci vars."""
    topology = json.loads((FIXTURES / "02-multi-team.json").read_text())
    inv = _write(topology, tmp_path, team_count=2)
    blank = inv["all"]["children"]["r42_blank_group"]["hosts"]
    h = blank[next(host for host in blank if "1-trainee" in host)]
    assert h["ansible_host"] == "192.168.141.200"
    assert h["r42_ci_netmask"] == 24
    assert h["r42_ci_gateway"] == "192.168.141.1"
    assert h["r42_net_bridge"] == "vmbr141"


def test_rejects_node_without_role(tmp_path):
    """VM/LXC nodes MUST have a role; preflight catches this but inventory_writer
    is also a defense layer."""
    topology = {
        "schema_version": "1.0",
        "kind": "gamenet",
        "naming_prefix": "x",
        "bridge_base": 140,
        "nodes": [
            {
                "id": "broken", "kind": "vm",
                "replication": {"scope": "shared"},
                "template_vmid": 9001, "config": {}, "attachments": []
                # no 'role' field
            }
        ],
    }
    out = tmp_path / "hosts.yml"
    with pytest.raises(ValueError, match="missing 'role'"):
        write_inventory(
            topology=topology, team_count=1, codename="X",
            proxmox_address="10.0.0.1", ssh_keys_dir=tmp_path, dest=out,
        )
