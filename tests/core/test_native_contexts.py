import json
from types import SimpleNamespace

import pytest

from app.core.errors import Range42Error


def context(tmp_path, monkeypatch):
    root = tmp_path / "contexts"
    ws = root / "lab-demo"
    for name in ("inventory", "secrets", "ssh_keys"):
        (ws / name).mkdir(parents=True)
    (ws / "inventory/inventory_default.yml").write_text(
        "all:\n  children:\n    proxmox:\n      hosts:\n        lab:\n          ansible_host: 192.0.2.10:8006\n")
    (ws / "secrets/vault_pass.txt").write_text("not-public")
    (ws / "secrets/default_vault.yml").write_text("$ANSIBLE_VAULT;1.1;AES256\n")
    (ws / "sourced_range42.sh").write_text('export RANGE42_INFRASTRUCTURE_CODENAME="lab"\nexport RANGE42_INFRASTRUCTURE_LAB="demo"\n')
    code = tmp_path / "source/demo"
    code.mkdir(parents=True)
    (ws / "scenario").symlink_to(code)
    script = tmp_path / "context.sh"
    script.write_text('range42-context() { echo "native context: $1"; }\n')
    monkeypatch.setenv("RANGE42_CONTEXT_ROOT", str(root))
    monkeypatch.setenv("RANGE42_CONTEXT_SCRIPT", str(script))
    monkeypatch.delenv("RANGE42_NATIVE_CONTEXTS_FILE", raising=False)
    host = SimpleNamespace(id="host", api_url="https://192.0.2.10:8006", node_name="pve")
    return ws, script, host


def test_context_discovery_matches_registered_host_without_exposing_secrets(tmp_path, monkeypatch):
    from app.core.native_contexts import available_contexts
    ws, _, host = context(tmp_path, monkeypatch)
    rows = available_contexts([host])
    assert rows[0]["id"] == ws.name
    assert rows[0]["codename"] == "lab"
    assert rows[0]["target_host_id"] == "host"
    assert rows[0]["ready"] is True
    assert "not-public" not in json.dumps(rows)
    assert str(ws) not in json.dumps(rows)


def test_context_mismatch_is_blocked_before_any_execution(tmp_path, monkeypatch):
    from app.core.native_contexts import resolve_context
    context(tmp_path, monkeypatch)
    with pytest.raises(Range42Error, match="target"):
        resolve_context("lab-demo", SimpleNamespace(id="wrong", api_url="https://192.0.2.99:8006"))


def test_incomplete_context_has_actionable_readiness_issue(tmp_path, monkeypatch):
    from app.core.native_contexts import available_contexts
    ws, _, host = context(tmp_path, monkeypatch)
    (ws / "secrets/vault_pass.txt").unlink()
    row = available_contexts([host])[0]
    assert row["ready"] is False
    assert any("vault" in issue.lower() for issue in row["issues"])


def test_explicit_context_registration_can_name_an_existing_workspace(tmp_path, monkeypatch):
    from app.core.native_contexts import available_contexts, resolve_context
    ws, script, host = context(tmp_path, monkeypatch)
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"version": 1, "contexts": [{
        "id": "training", "label": "Training cluster", "workspace": str(ws),
        "context_script": str(script), "inventory_variables": {"custom_setting": "local"},
    }]}))
    monkeypatch.setenv("RANGE42_NATIVE_CONTEXTS_FILE", str(registry))
    assert available_contexts([host])[0]["label"] == "Training cluster"
    resolved = resolve_context("training", host)
    assert resolved.workspace == ws
    assert resolved.inventory_variables["custom_setting"] == "local"


@pytest.mark.parametrize('fault', [None, 'missing_binding', 'duplicate_bridge', 'overlapping_network', 'gateway', 'template_in_guest_range', 'unknown_field'])
def test_workshop_profiles_are_strict_server_configuration_with_exact_target_bindings(tmp_path, monkeypatch, fault):
    from app.core.native_contexts import available_contexts
    ws, script, host = context(tmp_path, monkeypatch)
    profile = {'version': 1, 'id': 'approved-native', 'vmid_ranges': [[45000, 45039]],
        'proxmox_template': 9240, 'deployer_template': 9241, 'storage': 'lab-storage',
        'networks': [{'bridge': 'vmbr600', 'subnet': '10.78.0.0/28', 'gateway': '10.78.0.1'}]}
    entry = {'id': 'approved-context', 'workspace': str(ws), 'context_script': str(script),
        'target_host_id': host.id, 'target_node': host.node_name, 'target_api_url': host.api_url, 'workshop_profile': profile}
    if fault == 'missing_binding':
        del entry['target_host_id']
    elif fault == 'duplicate_bridge':
        profile['networks'].append({'bridge': 'vmbr600', 'subnet': '10.78.0.16/28', 'gateway': '10.78.0.17'})
    elif fault == 'overlapping_network':
        profile['networks'].append({'bridge': 'vmbr601', 'subnet': '10.78.0.0/28', 'gateway': '10.78.0.1'})
    elif fault == 'gateway':
        profile['networks'][0]['gateway'] = '10.78.0.2'
    elif fault == 'template_in_guest_range':
        profile['proxmox_template'] = 45001
    elif fault == 'unknown_field':
        profile['allow_any_bridge'] = True
    registry = tmp_path / 'registry.json'
    registry.write_text(json.dumps({'version': 1, 'contexts': [entry]}))
    monkeypatch.setenv('RANGE42_NATIVE_CONTEXTS_FILE', str(registry))
    if fault:
        with pytest.raises(Range42Error, match='invalid entry'):
            available_contexts([host])
    else:
        result, = available_contexts([host])
        assert result['ready'] and result['workshop_profile']['id'] == 'approved-native'
        assert 'infrastructure_guests' not in result['workshop_profile']
        assert 'not-public' not in json.dumps(result)


def test_explicit_context_binding_selects_one_of_two_profiles_on_the_same_api(tmp_path, monkeypatch):
    from app.core.native_contexts import available_contexts, resolve_context
    workspace, script, existing = context(tmp_path, monkeypatch)
    pool = SimpleNamespace(id='pool-profile', api_url=existing.api_url, node_name=existing.node_name)
    registry = tmp_path / 'registry.json'
    registry.write_text(json.dumps({'version': 1, 'contexts': [{
        'id': 'workshop-pool', 'label': 'Reusable provisioning', 'workspace': str(workspace),
        'context_script': str(script), 'target_host_id': pool.id, 'target_node': pool.node_name,
        'target_api_url': pool.api_url,
    }]}))
    monkeypatch.setenv('RANGE42_NATIVE_CONTEXTS_FILE', str(registry))
    rows = available_contexts([existing, pool])
    assert rows[0]['ready'] is True
    assert rows[0]['target_host_id'] == pool.id
    assert resolve_context('workshop-pool', pool).node_name == pool.node_name
    with pytest.raises(Range42Error, match='target'):
        resolve_context('workshop-pool', existing)


@pytest.mark.parametrize('field,value', [('node_name', 'another-node'), ('api_url', 'https://192.0.2.10:9443'),
                                      ('api_url', 'http://192.0.2.10:8006')])
def test_bound_native_context_rejects_registration_drift(tmp_path, monkeypatch, field, value):
    from app.core.native_contexts import available_contexts, resolve_context
    workspace, script, host = context(tmp_path, monkeypatch)
    registry = tmp_path / 'registry.json'
    registry.write_text(json.dumps({'version': 1, 'contexts': [{
        'id': 'bound', 'workspace': str(workspace), 'context_script': str(script),
        'target_host_id': host.id, 'target_node': host.node_name, 'target_api_url': host.api_url,
    }]}))
    monkeypatch.setenv('RANGE42_NATIVE_CONTEXTS_FILE', str(registry))
    setattr(host, field, value)
    assert available_contexts([host])[0]['ready'] is False
    with pytest.raises(Range42Error, match='target'):
        resolve_context('bound', host)


@pytest.mark.parametrize("identity", ["../lab-demo", "/lab-demo", "missing"])
def test_context_identifiers_never_become_arbitrary_paths(tmp_path, monkeypatch, identity):
    from app.core.native_contexts import resolve_context
    _, _, host = context(tmp_path, monkeypatch)
    with pytest.raises(Range42Error):
        resolve_context(identity, host)


def test_context_with_noncanonical_workspace_name_is_not_offered_as_ready(tmp_path, monkeypatch):
    from app.core.native_contexts import available_contexts
    ws, _, host = context(tmp_path, monkeypatch)
    ws.rename(ws.with_name("wrong-directory"))
    row = available_contexts([host])[0]
    assert row["ready"] is False
    assert any("codename" in issue for issue in row["issues"])


def test_context_without_native_runtime_executables_is_not_offered_as_ready(tmp_path, monkeypatch):
    from app.core.native_contexts import available_contexts
    _, _, host = context(tmp_path, monkeypatch)
    monkeypatch.setattr("shutil.which", lambda _: None)
    row = available_contexts([host])[0]
    assert row["ready"] is False
    assert any("zsh" in issue for issue in row["issues"])


def test_generated_only_cli_adapter_is_not_offered_for_native_deployments(tmp_path, monkeypatch):
    from app.core.native_contexts import available_contexts
    _, script, host = context(tmp_path, monkeypatch)
    script.write_text('if [[ -f "$scenario_target/main.yml" ]]; then\n    _r42_run_concrete main.yml\nfi\n')
    row = available_contexts([host])[0]
    assert row["ready"] is False
    assert any("native" in issue.lower() for issue in row["issues"])


def test_installer_binding_makes_discovered_workspace_explicit_without_registry_edits(tmp_path, monkeypatch):
    from app.core.native_contexts import _contexts, resolve_context
    workspace, _, host = context(tmp_path, monkeypatch)
    (workspace / '.range42-target.json').write_text(json.dumps({'version': 1,
        'target_host_id': host.id, 'target_node': host.node_name, 'target_api_url': host.api_url}))
    found, = _contexts()
    assert (found.target_host_id, found.target_node, found.target_api_url) == (host.id, host.node_name, host.api_url)
    host.node_name = 'different-node'
    with pytest.raises(Range42Error, match='target'):
        resolve_context(workspace.name, host)


@pytest.mark.parametrize('fault', ['missing_field', 'unknown_field', 'oversize', 'symlink', 'invalid_json', 'http', 'writable'])
def test_invalid_installer_binding_cannot_fall_back_to_unbound_discovery(tmp_path, monkeypatch, fault):
    from app.core.native_contexts import _contexts
    workspace, _, host = context(tmp_path, monkeypatch)
    binding = {'version': 1, 'target_host_id': host.id, 'target_node': host.node_name, 'target_api_url': host.api_url}
    path = workspace / '.range42-target.json'
    if fault == 'missing_field':
        binding.pop('target_node')
    elif fault == 'unknown_field':
        binding['workspace'] = '/etc'
    elif fault == 'http':
        binding['target_api_url'] = 'http://192.0.2.10:8006'
    path.write_text(json.dumps(binding))
    if fault == 'oversize':
        path.write_text(' ' * 16385)
    elif fault == 'invalid_json':
        path.write_text('{')
    elif fault == 'symlink':
        target = tmp_path / 'foreign.json'
        path.rename(target)
        path.symlink_to(target)
    elif fault == 'writable':
        path.chmod(0o666)
    with pytest.raises(Range42Error, match='binding|invalid'):
        _contexts()
