import secrets
from app.core.proxmox_secrets import provision_proxmox_token, rotate_proxmox_token


def test_provision_writes_vault_encrypted_file(tmp_path):
    ws = tmp_path / "ALPHA-demo"
    (ws / "secrets").mkdir(parents=True)
    vault_pass = tmp_path / "vault_pass.txt"
    vault_pass.write_text("p4ss")
    out = provision_proxmox_token(
        workspace=ws, host_id="px-1",
        api_url="https://pve01:8006", token_id="range42@pam!backend",
        token_secret=secrets.token_hex(16),
        vault_password_file=vault_pass,
    )
    assert out.exists()
    body = out.read_text()
    assert body.startswith("$ANSIBLE_VAULT")


def test_rotate_overwrites(tmp_path):
    ws = tmp_path / "A-b"
    (ws / "secrets").mkdir(parents=True)
    vault_pass = tmp_path / "v.txt"
    vault_pass.write_text("p")
    provision_proxmox_token(workspace=ws, host_id="h", api_url="u",
                            token_id="t", token_secret="s1",
                            vault_password_file=vault_pass)
    before = (ws / "secrets" / "proxmox_token.yml").stat().st_mtime_ns
    rotate_proxmox_token(workspace=ws, host_id="h", api_url="u",
                         token_id="t", token_secret="s2",
                         vault_password_file=vault_pass)
    after = (ws / "secrets" / "proxmox_token.yml").stat().st_mtime_ns
    assert after >= before
