"""Custom exception handlers for FastAPI.

Provides a verbose 422 validation error handler that logs the full
request body and field-level error details in a format compatible
with the deployer-ui frontend.
"""

import json
import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def make_validation_error_detail(err: dict) -> dict:
    """Convert a Pydantic validation error dict to the response format the UI expects.

    :param err: Single error dict from ``exc.errors()`` containing ``loc``,
        ``msg``, ``type``, ``input``, and ``ctx`` keys.
    :type err: dict
    :returns: Reformatted dict with ``field``, ``msg``, ``type``, ``input``,
        and ``ctx`` keys.
    :rtype: dict
    """
    return {
        "field": ".".join(str(p) for p in err.get("loc", [])),
        "msg": err.get("msg", ""),
        "type": err.get("type", ""),
        "input": err.get("input", None),
        "ctx": err.get("ctx", None),
    }


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Verbose 422 handler with debug logging.

    Logs the HTTP method, URL, raw request body, and each validation
    error at the ``ERROR`` level.  Returns a JSON response matching the
    deployer-ui expected format.

    :param request: The incoming FastAPI request object.
    :type request: Request
    :param exc: The validation exception raised by Pydantic.
    :type exc: RequestValidationError
    :returns: A 422 JSON response with a ``detail`` array of error objects.
    :rtype: JSONResponse
    """
    logger.error("422 on %s %s %s", request.method, request.url.path, request.url.query)

    try:
        raw = await request.body()
        if raw:
            body_text = raw.decode("utf-8", "ignore")
            if body_text.strip():
                try:
                    parsed = json.loads(body_text)
                    logger.error(
                        "Request body:\n%s",
                        json.dumps(parsed, indent=2, ensure_ascii=False),
                    )
                except json.JSONDecodeError:
                    logger.error("Request body (raw): %s", body_text)
            else:
                logger.error("Request body: <empty>")
    except Exception:
        logger.exception("Failed to read request body.")

    details = []
    for err in exc.errors():
        detail = make_validation_error_detail(err)
        logger.error(
            "field=%s | msg=%s | type=%s",
            detail["field"],
            detail["msg"],
            detail["type"],
        )
        details.append(detail)

    return JSONResponse(status_code=422, content={"detail": details})
