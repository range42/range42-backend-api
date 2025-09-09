#!/usr/bin/python
import inspect
import os, shutil, stat, tempfile
import sys
from pathlib import Path
import logging

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from app.routes import router as api_router

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import vault


debug = True
# debug = False


#### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####
# LIFESPAN ACTIONS :
#
# ACTIONS
#   - unlock vault using VAULT_PASSWORD_FILE (filepath) OR VAULT_PASSWORD
#

_VAULT_TMP_DIR: Path | None = None

#### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####

async def use_vault_password(vault_password: str):
    global _VAULT_TMP_DIR
    # create temp dir with prefix
    _VAULT_TMP_DIR = Path(tempfile.mkdtemp(prefix="vault-"))

    # write / chmod vault pass file in temp dir.
    f = _VAULT_TMP_DIR / "vault_pass.txt"
    f.write_text(vault_password)
    os.chmod(f, stat.S_IRUSR | stat.S_IWUSR)  # 0600

    # vault.set_vault_path(f)
    vault.set_vault_path(f)

    print(":: lifespan :: use - VAULT_PASSWORD")


async def use_vault_password_file(vault_password_file: str):
    p = Path(vault_password_file)
    if not p.exists():
        raise RuntimeError(f":: err - vault password file not found ! :: {p}")

    vault.set_vault_path(p)

    print(f":: lifespan :: use - VAULT_PASSWORD_FILE={p}")

#### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####

@asynccontextmanager
async def lifespan(app: FastAPI):

    global _VAULT_TMP_DIR

    vault_password_file = os.getenv("VAULT_PASSWORD_FILE")
    vault_password = os.getenv("VAULT_PASSWORD")

    #### #### #### #### #### #### #### #### #### #### #### ####
    if vault_password_file:
        await use_vault_password_file(vault_password_file)

    elif vault_password:
        await use_vault_password(vault_password)

    else:
        print(":: lifespan :: no vault password provided !")

    #### #### #### #### #### #### #### #### #### #### #### ####
    try:
        yield #

    finally:
        if _VAULT_TMP_DIR and _VAULT_TMP_DIR.exists():
            shutil.rmtree(_VAULT_TMP_DIR, ignore_errors=True)

#### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####
# START FAST API
#### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####


logger = logging.getLogger(__name__)

tags_metadata = [
    {"name": "proxmox", "description": "proxmox lifecycle actions"},
    {"name": "docker" , "description": "docker lifecycle actions"},
]

app = FastAPI(
    title           = "CR42 - API",
    lifespan        = lifespan,
    #
    docs_url        = "/docs/swagger",
    redoc_url       = "/docs/redoc",
    openapi_url     = "/docs/openapi.json",
    version         = "v0.1",
    #
    license_info    = { "name": "GPLv3" },
    contact         = { "email": "info@digisquad.com" },

    #
    # docs_url=None,   # to disable - swagger ui   - default location /docs
    # redoc_url=None,  # to disable - redoc        - default location /redoc
    # openapi_url=None # to disable - openapi json - default location /openapi.json
    #
)

#### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####
#
# disable the following in production to reduce attack surface with errors.
#

if debug:
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):

        logger.error("")
        logger.error(":: 422 on %s %s", request.method, request.url.path)

        try:
            body = (await request.body()).decode("utf-8", "ignore")
            logger.error("")
            logger.error("Request body: %s", body)
        except Exception:
            pass

        pretty = []

        for e in exc.errors():
            loc = ".".join(str(p) for p in e.get("loc", []))
            msg = e.get("msg")
            typ = e.get("type")
            ctx = e.get("ctx")
            inp = e.get("input")

            logger.error("")
            logger.error("field=%s | msg=%s | type=%s ", loc, msg, typ)
            logger.error("input=%r | ctx=%s",  inp, ctx)
            logger.error("")
            pretty.append({"field": loc, "msg": msg, "type": typ, "ctx": ctx})
            logger.error("")

        return JSONResponse(status_code=422, content={"detail": pretty})


#### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####
# include routers
#### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####

app.include_router(api_router)

#### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### #### ####

###
### SHOW DEBUG ROUTE :: list all routes ###
###

# for r in app.router.routes:
for r in app.routes:

    try:

        # GET HTTP VERBS
        verbs = ",".join(sorted(r.methods))

    except Exception:
        verbs = ""

    print(f" :: routes :: {verbs:16s} {getattr(r, 'path', '')}")

