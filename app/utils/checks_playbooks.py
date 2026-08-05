"""Playbook path validation and resolution.

Provides resolver functions for actions, bundles, and scenarios.  Each
function validates the action name against a strict regex, resolves the
file path, and checks for path traversal before returning the absolute
path to the playbook YAML file.
"""

import os
import re
from pathlib import Path

from fastapi import HTTPException

from app.core.logging import get_logger

logger = get_logger(__name__)

# Playbook name grammar. Segments are slash-separated and may contain the
# dotted ``<subject>.<verb>.<object>`` form used by the bundle naming grammar
# (range42-playbooks#133), e.g. ``generic/systems.baseline.docker_host``. A "." or
# ".." segment is rejected here (and traversal is caught again downstream by the
# is_relative_to check in _resolve_file).
_PLAYBOOK_NAME_REGEX = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")


def _warmup_checks(playbooks_dir_type: str) -> Path:
    """Validate and resolve the playbooks base directory.

    Reads the appropriate environment variable based on the directory
    type and verifies that the resolved path exists and is a directory.

    :param playbooks_dir_type: Either ``"www_app"`` or ``"public_github"``.
    :type playbooks_dir_type: str
    :returns: Resolved absolute path to the playbooks directory.
    :rtype: Path
    :raises HTTPException: 400 if the directory type is unknown, the
        environment variable is missing, or the directory does not exist.
    """
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
    """Resolve an action playbook file path.

    Looks for ``<playbooks_dir>/actions/<action_name>/main.yml`` after
    validating the action name format.

    :param action_name: Slash-separated action path (e.g. ``"vm/clone-template"``).
    :type action_name: str
    :param playbooks_dir_type: Either ``"www_app"`` or ``"public_github"``.
    :type playbooks_dir_type: str
    :returns: Absolute path to the action's ``main.yml``.
    :rtype: Path
    :raises HTTPException: 400 if the name is invalid, the file is missing,
        or a path traversal is detected.
    """

    playbooks_dir = _warmup_checks(playbooks_dir_type)
    actions_dir = (playbooks_dir / "actions").resolve()

    # reminder - the following will NOT be considered as valid :
    #                               ###
    #
    # - /vm/clone-template        # start  with  /
    # - vm/clone-template /       # ending with  /
    # - vm//clone-template        # double slash
    # - ubuntu/ins tall           # space
    # - ../../etc/passwd          # "." / ".." segments (traversal)
    #
    # Dots WITHIN a segment are allowed (e.g. software.install.docker).
    #

    main_filepath = _resolve_file(actions_dir, _PLAYBOOK_NAME_REGEX, action_name)

    return main_filepath


def resolve_bundles_playbook(action_name: str, playbooks_dir_type: str) -> Path:
    """Resolve a bundle playbook file path.

    Looks for ``<playbooks_dir>/bundles/<action_name>/main.yml`` after
    validating the action name format.

    :param action_name: Slash-separated bundle path (e.g. ``"generic/systems.baseline.docker_host"``).
    :type action_name: str
    :param playbooks_dir_type: Either ``"www_app"`` or ``"public_github"``.
    :type playbooks_dir_type: str
    :returns: Absolute path to the bundle's ``main.yml``.
    :rtype: Path
    :raises HTTPException: 400 if the name is invalid, the file is missing,
        or a path traversal is detected.
    """

    playbooks_dir = _warmup_checks(playbooks_dir_type)
    actions_dir = (playbooks_dir / "bundles").resolve()

    main_filepath = _resolve_file(actions_dir, _PLAYBOOK_NAME_REGEX, action_name)

    return main_filepath


def resolve_bundles_playbook_init_file(
    action_name: str, playbooks_dir_type: str
) -> Path:
    """Resolve a bundle's init playbook file path.

    Looks for ``<playbooks_dir>/bundles/<action_name>/init.yml`` instead
    of the default ``main.yml``.  Used by multi-step bundle execution
    that runs per-VM initialization before the main playbook.

    :param action_name: Slash-separated bundle path.
    :type action_name: str
    :param playbooks_dir_type: Either ``"www_app"`` or ``"public_github"``.
    :type playbooks_dir_type: str
    :returns: Absolute path to the bundle's ``init.yml``.
    :rtype: Path
    :raises HTTPException: 400 if the name is invalid, the file is missing,
        or a path traversal is detected.
    """

    playbooks_dir = _warmup_checks(playbooks_dir_type)
    actions_dir = (playbooks_dir / "bundles").resolve()

    main_filepath = _resolve_file(
        actions_dir, _PLAYBOOK_NAME_REGEX, action_name, is_init_yaml=True
    )

    return main_filepath


def resolve_scenarios_playbook(action_name: str, playbooks_dir_type: str) -> Path:
    """Resolve a scenario playbook file path.

    Looks for ``<playbooks_dir>/scenarios/<action_name>/main.yml`` after
    validating the action name format.

    :param action_name: Slash-separated scenario path (e.g. ``"demo_lab"``).
    :type action_name: str
    :param playbooks_dir_type: Either ``"www_app"`` or ``"public_github"``.
    :type playbooks_dir_type: str
    :returns: Absolute path to the scenario's ``main.yml``.
    :rtype: Path
    :raises HTTPException: 400 if the name is invalid, the file is missing,
        or a path traversal is detected.
    """

    playbooks_dir = _warmup_checks(playbooks_dir_type)
    scenarios_dir = (playbooks_dir / "scenarios").resolve()

    main_filepath = _resolve_file(scenarios_dir, _PLAYBOOK_NAME_REGEX, action_name)

    return main_filepath


####


def _resolve_file(
    actions_dir: Path,
    actions_regex_pattern: re.Pattern[str],
    action_name: str,
    *,
    is_init_yaml: bool = False,
) -> Path:
    """Validate an action name and resolve the corresponding playbook file.

    Performs regex validation, path resolution, traversal detection, and
    existence checks.

    :param actions_dir: Base directory for the action type (actions/bundles/scenarios).
    :type actions_dir: Path
    :param actions_regex_pattern: Compiled regex pattern for name validation.
    :type actions_regex_pattern: re.Pattern[str]
    :param action_name: The action name to resolve (e.g. ``"generic/systems.baseline.docker_host"``).
    :type action_name: str
    :param is_init_yaml: If ``True``, resolve ``init.yml`` instead of ``main.yml``.
    :type is_init_yaml: bool
    :returns: Absolute path to the resolved playbook file.
    :rtype: Path
    :raises HTTPException: 400 if the name format is invalid, a path
        traversal is detected, or the file does not exist.
    """
    #
    # REGEX CHECKS
    #

    if not actions_regex_pattern.fullmatch(action_name):
        err = f":: err - INVALID ACTION NAME FORMAT {action_name!r}"
        logger.error(err)
        raise HTTPException(status_code=400, detail=err)

    # The regex allows dots inside a segment, so a "." / ".." segment slips
    # through the format check -- reject it explicitly before resolving.
    if any(segment in (".", "..") for segment in action_name.split("/")):
        err = f":: err - INVALID ACTION NAME SEGMENT {action_name!r}"
        logger.error(err)
        raise HTTPException(status_code=400, detail=err)

    #
    #  init|main.yaml must exists.
    #

    # if not is_init_yaml:
    if is_init_yaml is False:
        main_filepath = (actions_dir / action_name / "main.yml").resolve(strict=True)
    else:
        main_filepath = (actions_dir / action_name / "init.yml").resolve(strict=True)

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
