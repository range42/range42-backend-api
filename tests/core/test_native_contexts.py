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
