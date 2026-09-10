"""Concrete runtime credentials come from the selected backend target only."""
from types import SimpleNamespace

import pytest

from app.core.errors import Range42Error


def runtime_module():
    import importlib
    try:
        module = importlib.import_module("app.core.scenario_runtime")
    except ModuleNotFoundError:
        pytest.fail("Concrete scenarios need runtime target and vault preparation")
    return module


def host(**changes):
    return SimpleNamespace(api_url="https://pve.example:8006/", node_name="pve",
                           token_ref="deployer@pve!ui=selected-target-secret", **changes)


def test_target_runtime_uses_selected_api_and_workspace_public_key(tmp_path, monkeypatch):
    runtime = runtime_module()
    monkeypatch.delenv("RANGE42_PROXMOX_SSH_USER", raising=False)
    pub = tmp_path / "ssh_keys/backend_keys/r42.LAB-content-deployer-key_alice.pub"
    pub.parent.mkdir(parents=True)
    pub.write_text("ssh-ed25519 AAAA test\n")
    variables = runtime.target_runtime_variables(host(), tmp_path)
    assert len(variables.pop("default_admin_vm_ci_password")) >= 32
    assert variables == {
        "proxmox_api_host": "pve.example:8006", "proxmox_node": "pve",
        "proxmox_api_user": "deployer@pve", "proxmox_api_token_id": "ui",
        "proxmox_api_token_secret": "selected-target-secret",
        "r42_proxmox_address": "pve.example", "r42_proxmox_ssh_user": "root",
        "default_admin_vm_ci_ssh_key": "ssh-ed25519 AAAA test",
        "deployer_cli_user_ssh_known_hosts": str(tmp_path / "ssh_keys/known_hosts"),
    }
    assert "selected-target-secret" not in "".join(p.read_text() for p in tmp_path.rglob("*") if p.is_file())


@pytest.mark.parametrize("token", ["", "not-a-token", "user!id=", "!id=secret", "user!=secret"])
def test_invalid_token_fails_without_reusing_vault_credentials(tmp_path, token):
    runtime = runtime_module()
    target = host()
    target.token_ref = token
    with pytest.raises(Range42Error) as error:
        runtime.target_runtime_variables(target, tmp_path)
    assert error.value.code == "PROJECT_TARGET_INVALID"
    assert token not in str(error.value) or token == ""


def test_ssh_user_is_backend_configurable_and_validated(tmp_path, monkeypatch):
    runtime = runtime_module()
    monkeypatch.setenv("RANGE42_PROXMOX_SSH_USER", "r42-jump")
    assert runtime.target_runtime_variables(host(), tmp_path)["r42_proxmox_ssh_user"] == "r42-jump"
    monkeypatch.setenv("RANGE42_PROXMOX_SSH_USER", "root -oProxyCommand=bad")
    with pytest.raises(Range42Error):
        runtime.target_runtime_variables(host(), tmp_path)


def test_empty_vault_is_private_temporary_and_never_overwrites_existing_file(tmp_path):
    runtime = runtime_module()
    placeholder = runtime.prepare_runtime_vault(tmp_path)
    path = tmp_path / "secrets/default_vault.yml"
    assert path.read_text() == "{}\n"
    assert path.stat().st_mode & 0o777 == 0o600
    runtime.cleanup_runtime_vault(placeholder)
    assert not path.exists()
    path.write_text("existing: user vault\n")
    assert runtime.prepare_runtime_vault(tmp_path) is None
    runtime.cleanup_runtime_vault(None)
    assert path.read_text() == "existing: user vault\n"


def test_runtime_vault_cleanup_preserves_a_user_replacement(tmp_path):
    runtime = runtime_module()
    placeholder = runtime.prepare_runtime_vault(tmp_path)
    path = tmp_path / "secrets/default_vault.yml"
    path.write_text("new: vault\n")
    runtime.cleanup_runtime_vault(placeholder)
    assert path.read_text() == "new: vault\n"


def test_missing_guest_password_gets_a_private_stable_random_value(tmp_path):
    runtime = runtime_module()
    first = runtime.target_runtime_variables(host(), tmp_path)["default_admin_vm_ci_password"]
    assert len(first) >= 32
    assert first != "supersecret"
    assert runtime.target_runtime_variables(host(), tmp_path)["default_admin_vm_ci_password"] == first
    other = runtime.target_runtime_variables(host(), tmp_path / "other")["default_admin_vm_ci_password"]
    assert other != first
    path = tmp_path / "secrets/guest_admin_password"
    assert path.read_text().strip() == first
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("encrypted", [False, True])
def test_configured_guest_password_is_preserved_without_generating_another(tmp_path, encrypted):
    from ansible.parsing.vault import VaultLib, VaultSecret
    runtime = runtime_module()
    root = tmp_path / "secrets"
    root.mkdir()
    value = b"default_admin_vm_ci_password: configured-test-password\n"
    if encrypted:
        vault = VaultLib([("default", VaultSecret(b"test-vault-password"))])
        value = vault.encrypt(value)
        (root / "vault_pass.txt").write_text("test-vault-password\n")
    (root / "default_vault.yml").write_bytes(value)
    assert runtime.target_runtime_variables(host(), tmp_path)["default_admin_vm_ci_password"] == "configured-test-password"
    assert not (root / "guest_admin_password").exists()


def test_unreadable_vault_does_not_silently_replace_a_configured_password(tmp_path):
    runtime = runtime_module()
    root = tmp_path / "secrets"
    root.mkdir()
    (root / "default_vault.yml").write_text("[invalid yaml\n")
    with pytest.raises(Range42Error) as exc:
        runtime.target_runtime_variables(host(), tmp_path)
    assert exc.value.code == "PROJECT_RUNTIME_INVALID"
    assert not (root / "guest_admin_password").exists()


@pytest.mark.parametrize("password", ["inline-configured-password", "literal-{{not_a_variable}}-password"])
def test_inline_vault_guest_password_is_decrypted_without_templating_its_contents(tmp_path, password):
    from ansible.parsing.vault import VaultLib, VaultSecret
    root = tmp_path / "secrets"
    root.mkdir()
    secret = VaultSecret(b"inline-vault-password")
    ciphertext = VaultLib([("default", secret)]).encrypt(password.encode()).decode()
    (root / "vault_pass.txt").write_text("inline-vault-password\n")
    contents = "default_admin_vm_ci_password: !vault |\n" + "".join(
        f"  {line}\n" for line in ciphertext.splitlines()
    )
    (root / "default_vault.yml").write_text(contents)
    assert runtime_module().target_runtime_variables(host(), tmp_path)["default_admin_vm_ci_password"] == password
    assert (root / "default_vault.yml").read_text() == contents
    assert not (root / "guest_admin_password").exists()


@pytest.mark.parametrize("encrypted", [False, True])
def test_guest_password_reference_resolves_to_the_value_used_for_runtime_redaction(tmp_path, encrypted):
    from ansible.parsing.vault import VaultLib, VaultSecret
    root = tmp_path / "secrets"
    root.mkdir()
    contents = b'admin:\n  password: referenced-configured-password\ndefault_admin_vm_ci_password: "{{ admin.password }}"\n'
    if encrypted:
        secret = VaultSecret(b"reference-vault-password")
        contents = VaultLib([("default", secret)]).encrypt(contents)
        (root / "vault_pass.txt").write_text("reference-vault-password\n")
    (root / "default_vault.yml").write_bytes(contents)
    assert runtime_module().target_runtime_variables(host(), tmp_path)["default_admin_vm_ci_password"] == "referenced-configured-password"
    assert (root / "default_vault.yml").read_bytes() == contents
    assert not (root / "guest_admin_password").exists()


def test_unknown_guest_password_reference_fails_closed_without_exposing_vault_values(tmp_path):
    root = tmp_path / "secrets"
    root.mkdir()
    (root / "default_vault.yml").write_text(
        'private_value: do-not-disclose-this-value\ndefault_admin_vm_ci_password: "{{ missing_password }}"\n'
    )
    with pytest.raises(Range42Error) as exc:
        runtime_module().target_runtime_variables(host(), tmp_path)
    assert exc.value.code == "PROJECT_RUNTIME_INVALID"
    assert "missing_password" not in str(exc.value)
    assert "do-not-disclose-this-value" not in str(exc.value)
    assert not (root / "guest_admin_password").exists()
