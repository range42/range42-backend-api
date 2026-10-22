"""Environment-owned network intent derived from an approved registered profile."""

from ipaddress import IPv4Network
import re

from app.core.errors import Range42Error
from app.core.provisioning_profiles import resource_profile


def refused():
    return Range42Error(
        status=409,
        code="ENVIRONMENT_NETWORK_REVIEW_REQUIRED",
        message="Select an original registered environment with its approved isolated pool and managed gateway network.",
    )


def compile_plan(context):
    profile = resource_profile(context)
    if (
        not re.fullmatch(r"env-[0-9a-f]{24}", context.id)
        or context.registration_issue
        or profile is None
        or not profile.networks
        or profile.managed_gateway is None
        or not context.target_node
        or not context.target_host_id
        or not context.target_api_url
    ):
        raise refused()
    rows = sorted(
        (row.model_dump() for row in profile.networks),
        key=lambda row: int(row["bridge"][4:]),
    )
    management = profile.managed_gateway
    bridges = [management.management_bridge, *(row["bridge"] for row in rows)]
    if any(
        not re.fullmatch(r"vmbr[1-9][0-9]{2,4}", name)
        or not 500 <= int(name[4:]) <= 65535
        for name in bridges
    ):
        raise refused()
    subnet = IPv4Network(management.management_subnet)
    return {
        "version": 1,
        "kind": "range42-environment-networks",
        "environment_id": context.id,
        "owner": "range42-environment:" + context.id,
        "node": context.target_node,
        "networks": rows,
        "management": {
            "bridge": management.management_bridge,
            "subnet": str(subnet),
            "host_address": f"{subnet[1]}/{subnet.prefixlen}",
        },
    }
