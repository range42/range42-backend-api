"""Optional, independent no-command SSH capability for environment networks."""

from app.core import preparation_transport as transport
from app.core.errors import Range42Error


def unavailable():
    return Range42Error(
        status=503,
        code="ENVIRONMENT_NETWORK_UNAVAILABLE",
        message="The optional environment network module is unavailable. Enroll its separate bounded service for this target before preparing bridges.",
    )


def configuration(host_id, node_name):
    try:
        return transport.fixed_configuration(
            host_id,
            node_name,
            registry_variable="RANGE42_ENVIRONMENT_NETWORK_AGENTS_FILE",
            alias_prefix="range42-env-network-",
        )
    except Range42Error:
        raise unavailable() from None


async def read(config, *, action, request=None):
    if action not in {"profile", "prepare", "status"} or (
        request is not None if action == "profile" else not isinstance(request, dict)
    ):
        raise unavailable()
    payload = {"action": action}
    if action != "profile":
        payload["request"] = request
    try:
        return await transport.read_fixed_service(
            config, payload, maximum=16384, timeout=15 if action == "profile" else 110
        )
    except Range42Error:
        raise unavailable() from None
