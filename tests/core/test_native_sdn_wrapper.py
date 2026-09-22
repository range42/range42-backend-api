"""Native NAT writes require supported source-rule shapes before the composite."""
import re
from types import SimpleNamespace

import pytest
import yaml

from tests.core.test_runtime_state import write_scenario


@pytest.mark.asyncio
async def test_native_nat_wrapper_checks_rule_shapes_before_mutation_and_observes_after(tmp_path, monkeypatch):
    from app.core import runtime_runner
    artifact = tmp_path / "artifact"
    scenario_dir = artifact / "checkout/scenario"
    scenario_dir.mkdir(parents=True)
    write_scenario(scenario_dir)
    plan = {"contract": "native-sdn-20260921", "vmids": [], "missing_vmids": [],
            "bundle": "proxmox/sdn_network.internet_on", "variables": {"BUNDLE_SDN_SUBNET_ID": "lab-10.42.70.0-24"}, "subnet": "10.42.70.0/24"}
    async def verified(*args):
        return plan
    monkeypatch.setattr(runtime_runner, "_verified_plan", verified)
    root = tmp_path / "bundles"
    bundle = root / plan["bundle"] / "main.yml"
    bundle.parent.mkdir(parents=True)
    bundle.write_text("- hosts: proxmox\n  tasks: []\n")
    monkeypatch.setenv("RANGE42_BUNDLE_DIR", str(root))
    run = await runtime_runner.prepare_runtime_run(SimpleNamespace(id="dep", scenario_label="test", workspace_path=str(tmp_path)),
        SimpleNamespace(operation={"request": {"kind": "sdn_snat"}}), None,
        SimpleNamespace(playbook=scenario_dir / "main.yml"), artifact)
    plays = yaml.safe_load(run.playbook.read_text())
    assert len(plays) == 4
    assert plays[0]["tasks"][0]["ansible.builtin.command"]["argv"] == ["hostname", "-s"]
    assert plays[1]["name"] == "Verify native SNAT rule compatibility before mutation"
    assert plays[2]["ansible.builtin.import_playbook"] == str(bundle)
    assert plays[3]["tasks"][-1]["ansible.builtin.debug"]["var"] == "r42_native_snat_observation"


@pytest.mark.parametrize("line,allowed", [
    ("-P POSTROUTING ACCEPT", True),
    ("-A POSTROUTING -s 10.42.70.0/24 -o vmbr0 -j SNAT --to-source 192.0.2.1", True),
    ("-A POSTROUTING -s 10.42.70.0/24 -o vmbr0 -j MASQUERADE", True),
    ("-A POSTROUTING -j OTHERCHAIN", True),
    ("-A POSTROUTING -s 10.42.70.0/24 -j ACCEPT", False),
    ("-A POSTROUTING ! -s 10.42.70.0/24 -o vmbr0 -j MASQUERADE", False),
    ('-A POSTROUTING -m comment --comment " -s 10.42.70.0/24 -j SNAT " -j ACCEPT', False),
    ("-A POSTROUTING -s 10.42.70.0/24 -m comment --comment test -o vmbr0 -j MASQUERADE", False),
    ("-A POSTROUTING -s 10.42.70.0/24 -p tcp --dport 80 -j MASQUERADE", False),
])
def test_native_rule_shape_guard_refuses_ambiguous_counts_or_source_only_deletion(line, allowed):
    from app.core import native_sdn
    assert bool(re.fullmatch(native_sdn.SUPPORTED_NATIVE_NAT_LINE, line)) is allowed
