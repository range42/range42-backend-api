"""Corroborate original controller receipts with actual verified HTTPS reads."""

import asyncio
import json
import re
from urllib.parse import quote

import httpx
from pydantic import ValidationError

from app.core import workshop_ownership as ownership
from app.core.errors import Range42Error
from app.core.preparation_transport import peer_certificate_digest, verify_api_peer
from app.core.proxmox_tls import proxmox_verify


def unavailable():
    return Range42Error(
        status=503,
        code="WORKSHOP_OWNERSHIP_UNAVAILABLE",
        message="Original guest ownership could not be verified. Preserve the original receipts and controller handoff; no VM operation was sent.",
    )


def configuration_projection(value, row):
    # Compare every supported NIC, address and disk attachment, not an event's
    # role names or VMID parity. The private/native adapter owns its policy.
    devices = {
        key
        for key in value
        if re.fullmatch(
            r"(?:(?:ide|sata|scsi|virtio|net|ipconfig)[0-9]+|efidisk0|tpmstate0)", key
        )
    }
    required = {"name", "description", "vmgenid", "cpu", "cores", "memory"} | devices
    if (
        value.get("template")
        or value.get("lock")
        or "args" in value
        or any(re.fullmatch(r"(?:hostpci|usb|unused)[0-9]+", key) for key in value)
        or not required <= value.keys()
    ):
        raise ownership.refused()
    return {key: value[key] for key in sorted(required)}


async def verify_guest_configurations(host, witness):
    semaphore = asyncio.Semaphore(4)
    async with httpx.AsyncClient(
        verify=proxmox_verify(), timeout=10, follow_redirects=False
    ) as client:

        async def check(row):
            async with semaphore:
                views = []
                for current in (1, 0):
                    url = (
                        host.api_url.rstrip("/")
                        + "/api2/json/nodes/"
                        + quote(witness.node_name, safe="")
                        + "/qemu/"
                        + str(row.vm_id)
                        + "/config"
                    )
                    async with client.stream(
                        "GET",
                        url,
                        params={"current": current},
                        headers={"Authorization": "PVEAPIToken=" + host.token_ref},
                    ) as response:
                        if (
                            response.status_code != 200
                            or peer_certificate_digest(response)
                            != witness.api_certificate_sha256
                        ):
                            raise ownership.refused()
                        raw = bytearray()
                        async for chunk in response.aiter_bytes():
                            raw.extend(chunk)
                            if len(raw) > 65536:
                                raise ownership.refused()
                        value = json.loads(raw)
                        if not isinstance(value, dict) or not isinstance(
                            value.get("data"), dict
                        ):
                            raise ownership.refused()
                        view = configuration_projection(value["data"], row)
                        if (
                            ownership.digest(view)
                            != row.generation.configuration_sha256
                        ):
                            raise ownership.refused()
                        views.append(view)
                if views[0] != views[1]:
                    raise ownership.refused()

        async with asyncio.timeout(60):
            tasks = [asyncio.create_task(check(row)) for row in witness.assignments]
            try:
                await asyncio.gather(*tasks)
            finally:
                # A mismatch must finish every concurrent observation before
                # closing the shared client or returning a failed review.
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)


async def observe(session, deployment_id):
    from app.routes.v1.admin import workshop

    _, host, _ = await ownership.binding(session, deployment_id)
    target = ownership.target_revision(host)
    boundary = await workshop.agent_request({"action": "admission"})
    if (
        boundary.get("scope") != "legacy_controller_queue"
        or boundary.get("state") != "paused"
        or type(boundary.get("queued_count")) is not int
        or type(boundary.get("running_count")) is not int
        or boundary["queued_count"] != 0
        or boundary["running_count"] != 0
    ):
        raise Range42Error(
            status=409,
            code="WORKSHOP_OWNERSHIP_PAUSE_REQUIRED",
            message="Pause participant-management operations and let accepted jobs finish before reviewing original guest ownership.",
        )
    try:
        witness = ownership.OwnershipWitness.model_validate(
            await workshop.agent_request({"action": "ownership"})
        )
        await verify_api_peer(host, witness)
        await verify_guest_configurations(host, witness)
    except (
        ValidationError,
        httpx.HTTPError,
        TimeoutError,
        ValueError,
        KeyError,
        TypeError,
    ):
        raise unavailable() from None
    return await ownership.review_observation(
        session, deployment_id, witness, observed_target_revision=target
    )
