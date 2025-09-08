
import os
from pathlib import Path
import re
from venv import logger

from fastapi import HTTPException


def resolve_inventory(inventory_name: str) -> Path:
    """ resolve inventory file path """

    project_root = Path(os.getenv("PROJECT_ROOT_DIR")).resolve()
    inventory_dir = (project_root / "inventory").resolve()

    # inventory_name_pattern = re.compile(r"^[A-Za-z0-9_-]+$")
    # inventory_name_pattern = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")
    inventory_name_pattern = re.compile(r"^[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*$")

    return _resolve_inventory_file(
        inventory_dir=inventory_dir,
        inventory_name=inventory_name,
        name_pattern=inventory_name_pattern,
    )

def _resolve_inventory_file(inventory_dir: Path,
                            inventory_name: str,
                            name_pattern: re.Pattern[str]) -> Path:

    if not name_pattern.fullmatch(str(inventory_name)):

        err = f":: err - INVALID INVENTORY NAME FORMAT {inventory_name!r}"
        logger.error(err)
        raise HTTPException(status_code=400, detail=err)

    inventory_filename = Path(str(inventory_name)).with_suffix(".yml")
    inventory_filepath = (inventory_dir / f"{inventory_filename}").resolve(strict=False)

    #
    # try to avoid traversal or symlinks injections :
    #

    if not inventory_filepath.is_relative_to(inventory_dir):

        err = f":: err - POTENTIAL PATH TRAVERSAL DETECTED : {inventory_filepath}"
        logger.error(err)
        raise HTTPException(status_code=400, detail=err)

    #
    # others sanity checks
    #

    if not inventory_dir.exists():

        err = f":: err - INVENTORY DIR  NOT FOUND : {inventory_dir}"
        logger.error(err)
        raise HTTPException(status_code=500, detail=err)

    #
    # check true resolve (ending)
    #
    try:
        inventory_filepath = inventory_filepath.resolve(strict=True)

    except FileNotFoundError:

        err = f":: err - INVENTORY NOT FOUND : {inventory_filepath}"
        logger.error(err)
        raise HTTPException(status_code=400, detail=err)

    logger.info(f":: ok - resolved inventory : {inventory_filepath}")

    return inventory_filepath



