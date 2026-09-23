"""Backend-owned runtime inputs for credential-free concrete scenarios."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
from urllib.parse import urlsplit

from app.core.errors import Range42Error
from app.core.models import ProxmoxHost


def prepare_scenario_context(workspace: Path, scenario_dir: Path, artifact_dir: Path) -> Path:
    """Expose native context paths without changing shared or pinned files."""
    context = artifact_dir / "config"
    context.mkdir(mode=0o700)
    (context / "secrets").symlink_to(workspace / "secrets", target_is_directory=True)
    (context / "scenario").symlink_to(scenario_dir, target_is_directory=True)
    return context


def _guest_password(workspace: Path) -> str:
    """Preserve a configured password, otherwise persist a private random one."""
    from ansible.errors import AnsibleError
    from ansible.parsing.dataloader import DataLoader
    from ansible.parsing.vault import VaultHelper, VaultLib, VaultSecret
    from ansible.template import Templar

    secret_dir = workspace / "secrets"
    vault_path = secret_dir / "default_vault.yml"
    loader = DataLoader()
    try:
        if vault_path.exists():
            vault_pass = secret_dir / "vault_pass.txt"
            vault_secrets = []
            if vault_pass.is_file():
                vault_secrets = [("default", VaultSecret(vault_pass.read_bytes().strip()))]
                loader.set_vault_secrets(vault_secrets)
            values = loader.load_from_file(str(vault_path), trusted_as_template=True)
            if not isinstance(values, dict):
                raise ValueError("vault must be a mapping")
            if "default_admin_vm_ci_password" in values:
                # Inline !vault values use a process-global secrets context
                # by default. Decrypt with this workspace's own VaultLib so
                # concurrent deployments cannot borrow another vault's key.
                vault = VaultLib(vault_secrets)

                def decrypt_inline(value):
                    ciphertext = VaultHelper.get_ciphertext(value, with_tags=False)
                    if ciphertext is not None:
                        # Decrypted scalars remain untrusted as templates,
                        # matching Ansible's literal !vault value semantics.
                        return vault.decrypt(ciphertext).decode("utf-8")
                    if isinstance(value, dict):
                        return {key: decrypt_inline(item) for key, item in value.items()}
                    if isinstance(value, list):
                        return [decrypt_inline(item) for item in value]
                    return value

                values = decrypt_inline(values)
                password = Templar(loader=loader, variables=values).template(
                    values["default_admin_vm_ci_password"], fail_on_undefined=True,
                )
                if not isinstance(password, str) or not password:
                    raise ValueError("guest password must be a non-empty string")
                return str(password)

        secret_dir.mkdir(parents=True, exist_ok=True)
        path = secret_dir / "guest_admin_password"
        # Publish complete content atomically without replacing an existing
        # secret, including when two preparations overlap before their lock.
        fd, temporary = tempfile.mkstemp(prefix=".guest-password-", dir=secret_dir)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(secrets.token_urlsafe(48) + "\n")
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
        finally:
            os.unlink(temporary)
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW)) as stream:
            mode = os.fstat(stream.fileno()).st_mode
            if not stat.S_ISREG(mode) or mode & 0o077:
                raise ValueError("generated password must be a private regular file")
            password = stream.read(1024).strip()
        if len(password) < 32 or len(password) >= 1024:
            raise ValueError("invalid generated password")
        return password
    except (AnsibleError, OSError, ValueError, TypeError):
        raise Range42Error(code="PROJECT_RUNTIME_INVALID", error="invalid_runtime",
                           message="Cannot read the workspace vault or private guest password. Check its format, vault password and file permissions.") from None
    finally:
        loader.cleanup_all_tmp_files()


def target_runtime_variables(host: ProxmoxHost, workspace: Path) -> dict[str, str | bool]:
    """Extra vars have precedence over old vault and committed inventory values."""
    def invalid(message: str):
        return Range42Error(code="PROJECT_TARGET_INVALID", error="invalid_target", message=message)

    if host is None:
        raise invalid("The selected Proxmox target is unavailable")
    try:
        api = urlsplit(str(host.api_url))
        address = api.hostname
        if api.scheme not in ("http", "https") or not address or api.username or api.password:
            raise ValueError("invalid API address")
        api.port  # Validate a malformed port before passing it to the runner.
        identity, secret = host.token_ref.split("=", 1)
        user, token_id = identity.split("!", 1)
        if not user or not token_id or not secret:
            raise ValueError("invalid token")
    except (ValueError, AttributeError):
        raise invalid("The selected target requires an HTTP(S) API address and a user!token-id=secret API token") from None
    ssh_user = os.getenv("RANGE42_PROXMOX_SSH_USER", "root")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", ssh_user):
        raise invalid("RANGE42_PROXMOX_SSH_USER must be an SSH account name")
    variables = {
        "proxmox_api_host": api.netloc,
        "proxmox_node": host.node_name,
        "proxmox_api_user": user,
        "proxmox_api_token_id": token_id,
        "proxmox_api_token_secret": secret,
        "proxmox_api_validate_certs": True,
        "r42_proxmox_address": f"[{address}]" if ":" in address else address,
        "r42_proxmox_ssh_user": ssh_user,
        "deployer_cli_user_ssh_known_hosts": str(workspace / "ssh_keys/known_hosts"),
    }
    public_keys = sorted((workspace / "ssh_keys/backend_keys").glob("*deployer-key_alice.pub"))
    if len(public_keys) == 1:
        variables["default_admin_vm_ci_ssh_key"] = public_keys[0].read_text().strip()
    elif len(public_keys) > 1:
        raise invalid("The workspace has multiple admin SSH public keys; select one before deployment")
    variables["default_admin_vm_ci_password"] = _guest_password(workspace)
    return variables


@dataclass(frozen=True)
class RuntimeVaultPlaceholder:
    path: Path
    device: int
    inode: int


def prepare_runtime_vault(workspace: Path) -> RuntimeVaultPlaceholder | None:
    """Satisfy bundle vars_files without replacing a configured user vault."""
    path = workspace / "secrets/default_vault.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return None
    with os.fdopen(fd, "w") as stream:
        stream.write("{}\n")
        stat = os.fstat(stream.fileno())
    return RuntimeVaultPlaceholder(path, stat.st_dev, stat.st_ino)


def cleanup_runtime_vault(placeholder: RuntimeVaultPlaceholder | None) -> None:
    if placeholder is None:
        return
    try:
        stat = placeholder.path.lstat()
        if ((stat.st_dev, stat.st_ino) == (placeholder.device, placeholder.inode)
                and placeholder.path.read_text() == "{}\n"):
            placeholder.path.unlink()
    except OSError:
        pass
