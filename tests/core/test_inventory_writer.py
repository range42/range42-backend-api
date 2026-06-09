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
