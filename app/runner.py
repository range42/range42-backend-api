
import os, shutil, tempfile
from pathlib import Path
from fastapi.responses import JSONResponse
from ansible_runner import run

from . import utils
from . import vault


def print_env_vars(envvars: dict):
    print(f"ANSIBLE_ROLES_PATH           : {envvars.get('ANSIBLE_ROLES_PATH', '')}")
    print(f"ANSIBLE_COLLECTIONS_PATH     : {envvars.get('ANSIBLE_COLLECTIONS_PATH','')}")
    print(f"ANSIBLE_COLLECTIONS_PATHS    : {envvars.get('ANSIBLE_COLLECTIONS_PATHS','')}")
    print(f"ANSIBLE_LIBRARY              : {envvars.get('ANSIBLE_LIBRARY', '')}")
    print(f"ANSIBLE_FILTER_PLUGINS       : {envvars.get('ANSIBLE_FILTER_PLUGINS', '')}")
    print(f"ANSIBLE_HOST_KEY_CHECKING    : {envvars.get('ANSIBLE_HOST_KEY_CHECKING', 'False')}")
    print(f"ANSIBLE_DEPRECATION_WARNINGS : {envvars.get('ANSIBLE_DEPRECATION_WARNINGS', 'False')}")
    print(f"PYTHONWARNINGS               : {envvars.get('PYTHONWARNINGS', 'ignore::DeprecationWarning')}")
    print(f"ANSIBLE_INVENTORY_ENABLED    : {envvars.get('ANSIBLE_INVENTORY_ENABLED', 'yaml,ini')}")


def set_env_vars(tmp_dir: Path):
    """ write env file in tmp_dir """
    # envvars = {}
    #
    # if os.getenv("ANSIBLE_ROLES_PATH"):            envvars["ANSIBLE_ROLES_PATH"]           = os.environ["ANSIBLE_ROLES_PATH"]
    # if os.getenv("ANSIBLE_COLLECTIONS_PATHS"):     envvars["ANSIBLE_COLLECTIONS_PATHS"]    = os.environ["ANSIBLE_COLLECTIONS_PATHS"]
    # if os.getenv("ANSIBLE_LIBRARY"):               envvars["ANSIBLE_LIBRARY"]              = os.environ["ANSIBLE_LIBRARY"]
    # if os.getenv("ANSIBLE_FILTER_PLUGINS"):        envvars["ANSIBLE_FILTER_PLUGINS"]       = os.environ["ANSIBLE_FILTER_PLUGINS"]
    # if os.getenv("ANSIBLE_HOST_KEY_CHECKING"):     envvars["ANSIBLE_HOST_KEY_CHECKING"]    = os.environ["ANSIBLE_HOST_KEY_CHECKING"]
    # if os.getenv("ANSIBLE_DEPRECATION_WARNINGS"):  envvars["ANSIBLE_DEPRECATION_WARNINGS"] = os.environ["ANSIBLE_DEPRECATION_WARNINGS"]
    # if os.getenv("ANSIBLE_INVENTORY_ENABLED"):     envvars["ANSIBLE_INVENTORY_ENABLED"]    = os.environ["ANSIBLE_INVENTORY_ENABLED"]
    # if os.getenv("PYTHONWARNINGS"):                envvars["PYTHONWARNINGS"]               = os.environ["PYTHONWARNINGS"]

    # maybe useful later.
    #
    # if os.getenv("ANSIBLE_CONFIG"): envvars["ANSIBLE_CONFIG"] = os.environ["ANSIBLE_CONFIG"]
    # if os.getenv("ANSIBLE_CALLBACK_PLUGINS"): envvars["ANSIBLE_CALLBACK_PLUGINS"] = os.environ["ANSIBLE_CALLBACK_PLUGINS"]
    # #if os.getenv("ANSIBLE_STDOUT_CALLBACK"): envvars["ANSIBLE_STDOUT_CALLBACK"] = os.environ["ANSIBLE_STDOUT_CALLBACK"]
    #

    home_collections = os.path.expanduser("~/.ansible/collections")
    sys_collections = "/usr/share/ansible/collections"
    coll_paths = f"{home_collections}:{sys_collections}"

    envvars = {
        "ANSIBLE_HOST_KEY_CHECKING": "True",
        "ANSIBLE_DEPRECATION_WARNINGS": "False",
        "ANSIBLE_INVENTORY_ENABLED": "yaml,ini",
        "PYTHONWARNINGS": "ignore::DeprecationWarning",
        "ANSIBLE_ROLES_PATH": os.environ.get("ANSIBLE_ROLES_PATH", ""),
        # "ANSIBLE_COLLECTIONS_PATHS": os.environ.get("ANSIBLE_COLLECTIONS_PATHS", "/home/grml/_products.git-hyde-repo/range42-backend-api/collections/"),
        # "ANSIBLE_LIBRARY": os.environ.get("ANSIBLE_LIBRARY", ""),
        "ANSIBLE_FILTER_PLUGINS": os.environ.get("ANSIBLE_FILTER_PLUGINS", ""),

        # ⬇️ corrections
        "ANSIBLE_COLLECTIONS_PATH": os.environ.get("ANSIBLE_COLLECTIONS_PATH", coll_paths),
        "ANSIBLE_COLLECTIONS_PATHS": os.environ.get("ANSIBLE_COLLECTIONS_PATHS", coll_paths),
        # ⬆️ corrections
        "ANSIBLE_LIBRARY": os.environ.get("ANSIBLE_LIBRARY", ""),
    }

    # VAULT ENV VARS - SET PRIORITY ENV VAR FIRST
    if os.getenv("VAULT_PASSWORD_FILE"):
        envvars["ANSIBLE_VAULT_PASSWORD_FILE"] = os.environ["VAULT_PASSWORD_FILE"]

    elif vault.get_vault_path():
        envvars["ANSIBLE_VAULT_PASSWORD_FILE"] = str(vault.get_vault_path())


    if os.getenv("ANSIBLE_CONFIG"):
        envvars["ANSIBLE_CONFIG"] = os.environ["ANSIBLE_CONFIG"]


    # WRITE ENV IN tmp_dir/env/envvars
    env_dir = tmp_dir / "env"
    env_dir.mkdir(parents=True, exist_ok=True)
    env_file = env_dir / "envvars"
    env_file.write_text(
        "\n".join(f"{k}={v}" for k, v in envvars.items()) + "\n"
    )

    print_env_vars(envvars)


def create_temp_dir(inventory: Path, playbook: Path, tmp_dir: Path) -> tuple[Path, Path]:
    """
    copy playbook files ___TREE___ to /tmp_dir
    return : inventory_path_in_tmp, playbook_path
    """
    project_dir = tmp_dir / "project"
    inventory_dir = tmp_dir / "inventory"
    project_dir.mkdir(parents=True, exist_ok=True)
    inventory_dir.mkdir(parents=True, exist_ok=True)

    src_dir = playbook.parent
    dst_dir = project_dir / src_dir.name
    shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)

    play_rel = (dst_dir / playbook.name).relative_to(project_dir)
    inv_dest = inventory_dir / inventory.name
    shutil.copy(inventory, inv_dest)

    set_env_vars(tmp_dir)

    return inv_dest, play_rel

def build_logs(events) -> tuple[str, str]:
    """ build ansible plain logs with/without ansi"""

    # lines = [
    #     ev.get("stdout")
    #     for ev in events if ev.get("stdout")
    # ]

    lines = []
    for ev in events:
        stdout = ev.get("stdout")
        if stdout:
            lines.append(stdout)

    text_ansi = "\n".join(lines).strip()
    return text_ansi, utils.strip_ansi(text_ansi)


####
####
####

def run_playbook_core(
    playbook: Path, inventory: Path,
    limit: str | None = None,
    cmdline: str | None = None,
    extravars: dict | None = None,
    quiet: bool = False,
):

    tmp_dir = Path(tempfile.mkdtemp(prefix="runner-"))
    try:
        inv_dest, play_rel = create_temp_dir(inventory, playbook, tmp_dir)

        # Auto vault
        # if not cmdline:
        #     vf = os.getenv("VAULT_PASSWORD_FILE") or (str(vault.get_vault_path()) if vault.get_vault_path() else None)
        #     if vf:
        #         cmdline = f'--vault-password-file "{vf}"'

        if not cmdline:
            vf = os.getenv("VAULT_PASSWORD_FILE")
            if not vf:
                vp = vault.get_vault_path()
                vf = str(vp) if vp else None

            if vf:
                cmdline = f'--vault-password-file "{vf}"'

        vars_file = os.getenv("API_BACKEND_VAULT_FILE")  #
        if vars_file:

            # will add the vault file as arg like :  ansible-playbook ping.yml -e @/etc/ansible/my_vars.yml
            # note : following the ansible doc => no quote with @file
            cmdline = f'{(cmdline or "").strip()} -e "@{vars_file}"'.strip()


        # print (" ---------------------------------------------")
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
        # print (" ---------------------------------------------")

        # events = list(r.events) if hasattr(r, "events") else []

        if hasattr(r, "events"):
            events = list(r.events)
        else:
            events = []


        log_ansi, log_plain = build_logs(events)
        return r.rc, events, log_plain, log_ansi
    finally:
        print (f" :: work done :: {tmp_dir}")
        # shutil.rmtree(tmp_dir, ignore_errors=True)

def run_playbook(
    playbook: Path, inventory: Path,
    limit: str | None = None,
    cmdline: str | None = None,
    extravars: dict | None = None,
    as_json: bool = False,
) -> JSONResponse:

    rc, events, log_plain, log_ansi = run_playbook_core(playbook, inventory, limit, cmdline, extravars)
    # payload = {"rc": rc, "events": events} if as_json else {"rc": rc, "log_plain": log_plain, "log_ansi": log_ansi}

    # if as_json:
    #     payload = {
    #         "rc": rc,
    #         "events": events,
    #     }
    # else:
    #     payload = {
    #         "rc": rc,
    #         "log_plain": log_plain,
    #         "log_ansi": log_ansi,
    #     }

    if as_json:
        payload = {
            "rc": rc,
            "events": events,  # brut
            # "result": extract_action_results(events, "vm_list"),
    }
    else:
        payload = {
            "rc": rc,
            "log_plain": log_plain,
            "log_ansi": log_ansi,
        }

    #### STATUS

    if rc == 0:
        status = 200
    else:
        status = 500

    return JSONResponse(payload, status_code=status)
    return JSONResponse(payload, status_code=200 if rc == 0 else 500)
    return JSONResponse(payload, status_code=200 if rc == 0 else 500)

