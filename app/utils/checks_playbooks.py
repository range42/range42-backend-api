import re
from venv import logger
import os

from fastapi import HTTPException
from pathlib import Path

####

def _warmup_checks(playbooks_dir_type: str) -> Path:
    playbooks_dir: Path

    if playbooks_dir_type == "www_app":
        raw = os.getenv("API_BACKEND_WWWAPP_PLAYBOOKS_DIR")

    elif playbooks_dir_type == "public_github":
        raw = os.getenv("API_BACKEND_PUBLIC_PLAYBOOKS_DIR")
    else:
        err = f":: err - Unknown playbooks_dir_type : {playbooks_dir_type!r}"
        logger.error(err)
        raise HTTPException(status_code=400, detail=err)

    if not raw:
        err = f":: err - Missing env var for {playbooks_dir_type}"
        logger.error(err)
        raise HTTPException(status_code=400, detail=err)

    playbooks_dir = Path(raw).resolve()

    if not playbooks_dir.exists() or not playbooks_dir.is_dir():
        err = f"Invalid playbooks dir: {playbooks_dir}"
        logger.error(err)
        raise HTTPException(status_code=400, detail=err)
    return playbooks_dir


def resolve_actions_playbook(action_name: str, playbooks_dir_type: str) -> Path:
    """ resolve actions file path """

    playbooks_dir = _warmup_checks(playbooks_dir_type)
    actions_dir = (playbooks_dir / "actions").resolve()

    # reminder - the following will NOT be considered as valid :
    #                               ###
    #
    # - /vm/clone-template        # start  with  /
    # - vm/clone-template /       # ending with  /
    # - vm//clone-template        # double slash
    # - linux/ubuntu/install.dot  # dot not allowed
    # - ubuntu/ins tall           # space
    #

    actions_regex_pattern = re.compile(r"^[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*$")
    main_filepath = _resolve_file(actions_dir, actions_regex_pattern, action_name)

    return main_filepath

def resolve_bundles_playbook(action_name: str, playbooks_dir_type: str) -> Path:
    """ resolve bundles file path """

    playbooks_dir = _warmup_checks(playbooks_dir_type)
    actions_dir = (playbooks_dir / "bundles").resolve()

    # print (actions_dir)

    actions_regex_pattern = re.compile(r"^[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*$")
    main_filepath = _resolve_file(actions_dir, actions_regex_pattern, action_name)

    return main_filepath


def resolve_scenarios_playbook(action_name: str, playbooks_dir_type: str) -> Path:
    """ resolve scenarios file path """

    playbooks_dir = _warmup_checks(playbooks_dir_type)
    scenarios_dir = (playbooks_dir / "scenarios").resolve()

    scenarios_regex_pattern = re.compile(r"^[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*$")
    main_filepath = _resolve_file(scenarios_dir, scenarios_regex_pattern, action_name)

    return main_filepath


####


def _resolve_file(actions_dir: Path,
                 actions_regex_pattern: re.Pattern[str],
                 action_name: str) -> Path:
    #
    # REGEX CHECKS
    #

    if not actions_regex_pattern.fullmatch(action_name):

        err = f":: err - INVALID ACTION NAME FORMAT {action_name!r}"
        logger.error(err)
        raise HTTPException(status_code=400, detail=err)

    #
    #  main.yAml must exists.
    #

    main_filepath = (actions_dir / action_name / "main.yml").resolve(strict=True)

    #
    # checks - attempt to avoid file - path traversal injections + symlinks injections
    #

    if not main_filepath.is_relative_to(actions_dir):

        err = f":: err - POTENTIAL PATH TRAVERSAL DETECTED : {main_filepath}"
        logger.error(err)
        raise HTTPException(status_code=400, detail=err)

    # if not project_root.exists():
    #
    #     err = f":: err - PROJECT ROOT NOT FOUND : {main_filepath}"
    #     logger.error(err)
    #     raise HTTPException(status_code=400, detail=err)

    if not main_filepath.exists():

        err = f":: err - PLAYBOOK NOT FOUND : {main_filepath}"
        logger.error(err)
        raise HTTPException(status_code=400, detail=err)

    return main_filepath

