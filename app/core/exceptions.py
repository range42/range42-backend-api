"""Custom exception handlers for FastAPI."""

import json
import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def make_validation_error_detail(err: dict) -> dict:
    """Convert a Pydantic validation error to the response format the UI expects."""
    return {
        "field": ".".join(str(p) for p in err.get("loc", [])),
        "msg": err.get("msg", ""),
        "type": err.get("type", ""),
        "input": err.get("input", None),
        "ctx": err.get("ctx", None),
    }


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Verbose 422 handler with debug logging. Matches deployer-ui expected format."""
    logger.error("422 on %s %s %s", request.method, request.url.path, request.url.query)

    try:
        raw = await request.body()
        if raw:
            body_text = raw.decode("utf-8", "ignore")
            if body_text.strip():
                try:
                    parsed = json.loads(body_text)
                    logger.error("Request body:\n%s", json.dumps(parsed, indent=2, ensure_ascii=False))
                except json.JSONDecodeError:
                    logger.error("Request body (raw): %s", body_text)
            else:
                logger.error("Request body: <empty>")
    except Exception:
        logger.exception("Failed to read request body.")

    details = []
    for err in exc.errors():
        detail = make_validation_error_detail(err)
        logger.error("field=%s | msg=%s | type=%s", detail["field"], detail["msg"], detail["type"])
        details.append(detail)

    return JSONResponse(status_code=422, content={"detail": details})
