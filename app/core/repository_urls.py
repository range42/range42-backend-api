"""Operator-approved Git endpoints shared by source and legacy project paths."""
from __future__ import annotations

from urllib.parse import urlsplit

from app.core.config import Settings
from app.core.errors import Range42Error

# Git must not redirect an approved endpoint to an arbitrary internal service.
GIT_HTTP_ENV = {
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "http.followRedirects",
    "GIT_CONFIG_VALUE_0": "false",
    "GIT_TERMINAL_PROMPT": "0",
}


def validate_repository_url(value: str) -> str:
    settings = Settings()
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Use a valid Git server URL") from exc
    schemes = {"https", "http"} if settings.git_allow_http else {"https"}
    if (parsed.scheme not in schemes or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or "\\" in value or any(ord(char) < 33 for char in value)):
        raise ValueError("Use an approved HTTPS Git server URL without credentials, query, or fragment")
    hostname = parsed.hostname.lower().rstrip(".")
    authority = f"[{hostname}]" if ":" in hostname else hostname
    if port and port != (443 if parsed.scheme == "https" else 80):
        authority += f":{port}"
    if authority not in settings.git_allowed_hosts:
        raise ValueError("Git server is not allowed; add its host[:port] to RANGE42_GIT_ALLOWED_HOSTS")
    return value


def require_repository_url(value: str) -> str:
    """Runtime guard also covers projects/sources created before validation."""
    try:
        return validate_repository_url(value)
    except ValueError as exc:
        raise Range42Error(code="GIT_URL_REJECTED", error="repository_url_rejected",
                           status=400, message=str(exc)) from None
