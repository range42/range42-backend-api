"""Clean playbook runner. Fixes temp-dir leak, removes print debugging, uses VaultManager."""

import os
import shutil
import tempfile
from pathlib import Path

from ansible_runner import run

from app.core.vault import VaultManager
from app.utils.text_cleaner import strip_ansi

vault_manager = VaultManager()


def build_logs(events) -> tuple[str, str]:
    """Build ansible log strings with and without ANSI escape codes."""
    lines = []
    for ev in events:
        stdout = ev.get("stdout")
        if stdout:
            lines.append(stdout)

    text_ansi = "\n".join(lines).strip()
    return text_ansi, strip_ansi(text_ansi)


def _build_envvars(vm: VaultManager) -> dict:
    """Build the Ansible environment variables dict."""
    home_collections = os.path.expanduser("~/.ansible/collections")
    sys_collections = "/usr/share/ansible/collections"
    coll_paths = f"{home_collections}:{sys_collections}"

    envvars = {
        "ANSIBLE_HOST_KEY_CHECKING": "True",
        "ANSIBLE_DEPRECATION_WARNINGS": "False",
        "ANSIBLE_INVENTORY_ENABLED": "yaml,ini",
        "PYTHONWARNINGS": "ignore::DeprecationWarning",
        "ANSIBLE_ROLES_PATH": os.environ.get("ANSIBLE_ROLES_PATH", ""),
        "ANSIBLE_FILTER_PLUGINS": os.environ.get("ANSIBLE_FILTER_PLUGINS", ""),
        "ANSIBLE_COLLECTIONS_PATH": os.environ.get("ANSIBLE_COLLECTIONS_PATH", coll_paths),
        "ANSIBLE_COLLECTIONS_PATHS": os.environ.get("ANSIBLE_COLLECTIONS_PATHS", coll_paths),
        "ANSIBLE_LIBRARY": os.environ.get("ANSIBLE_LIBRARY", ""),
    }

    # Vault password file
    if os.getenv("VAULT_PASSWORD_FILE"):
        envvars["ANSIBLE_VAULT_PASSWORD_FILE"] = os.environ["VAULT_PASSWORD_FILE"]
    elif vm.get_vault_path():
        envvars["ANSIBLE_VAULT_PASSWORD_FILE"] = str(vm.get_vault_path())

    if os.getenv("ANSIBLE_CONFIG"):
        envvars["ANSIBLE_CONFIG"] = os.environ["ANSIBLE_CONFIG"]

    return envvars


def _setup_temp_dir(
    inventory: Path, playbook: Path, vm: VaultManager,
) -> tuple[Path, Path, Path]:
    """Create temp dir, copy playbook tree and inventory, write envvars.

    Returns (tmp_dir, inventory_path_in_tmp, playbook_relative_path).
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="runner-"))

    project_dir = tmp_dir / "project"
    inventory_dir = tmp_dir / "inventory"
    project_dir.mkdir(parents=True, exist_ok=True)
    inventory_dir.mkdir(parents=True, exist_ok=True)

    # Copy the playbook's parent directory into project/
    src_dir = playbook.parent
    dst_dir = project_dir / src_dir.name
    shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)

    play_rel = (dst_dir / playbook.name).relative_to(project_dir)
    inv_dest = inventory_dir / inventory.name
    shutil.copy(inventory, inv_dest)

    # Write envvars file
    envvars = _build_envvars(vm)
    env_dir = tmp_dir / "env"
    env_dir.mkdir(parents=True, exist_ok=True)
    env_file = env_dir / "envvars"
    env_file.write_text(
        "\n".join(f"{k}={v}" for k, v in envvars.items()) + "\n"
    )

    return tmp_dir, inv_dest, play_rel


def _build_cmdline(
    vm: VaultManager,
    cmdline: str | None,
    tags: str | None,
) -> str | None:
    """Build the ansible-runner cmdline string."""
    if not cmdline:
        vf = os.getenv("VAULT_PASSWORD_FILE")
        if not vf:
            vp = vm.get_vault_path()
            vf = str(vp) if vp else None
        if vf:
            cmdline = f'--vault-password-file "{vf}"'

    vars_file = os.getenv("API_BACKEND_VAULT_FILE")
    if vars_file:
        cmdline = f'{(cmdline or "").strip()} -e "@{vars_file}"'.strip()

    if tags:
        cmdline = f'{(cmdline or "").strip()} --tags {tags}'.strip()

    return cmdline


def run_playbook_core(
    playbook: Path,
    inventory: Path,
    limit: str | None = None,
    tags: str | None = None,
    cmdline: str | None = None,
    extravars: dict | None = None,
    quiet: bool = False,
) -> tuple[int, list, str, str]:
    """Run an Ansible playbook and return (rc, events, log_plain, log_ansi).

    Uses the module-level vault_manager instance.
    Cleans up the temp directory in a finally block.
    """
    tmp_dir, inv_dest, play_rel = _setup_temp_dir(inventory, playbook, vault_manager)
    try:
        cmdline = _build_cmdline(vault_manager, cmdline, tags)

        r = run(
            private_data_dir=str(tmp_dir),
            playbook=str(play_rel),
            inventory=str(inv_dest),
            streamer="json",
            limit=limit,
            cmdline=cmdline,
            extravars=extravars or {},
            quiet=quiet,
        )

        events = list(r.events) if hasattr(r, "events") else []
        log_ansi, log_plain = build_logs(events)
        return r.rc, events, log_plain, log_ansi
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
