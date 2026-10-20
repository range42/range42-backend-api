"""Optional, fixed inspection transport; no browser paths or root commands."""

import asyncio
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import quote

import httpx

from app.core.errors import Range42Error
from app.core.physical_nodes import NodeAttestation, digest
from app.core.proxmox_tls import proxmox_verify

LIMIT = 16384


def unavailable():
    return Range42Error(
        status=503,
        code="PREPARATION_AGENT_UNAVAILABLE",
        message="The bounded node inspector is unavailable or not enrolled. Configure its dedicated installation capability before inspecting this target.",
    )


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate field")
        result[key] = value
    return result


def protected_bytes(path, *, maximum=65536):
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid not in {0, os.geteuid()}
            or info.st_nlink != 1
            or info.st_mode & 0o077
            or info.st_size > maximum
        ):
            raise ValueError()
        raw = os.read(descriptor, maximum + 1)
        if len(raw) > maximum:
            raise ValueError()
        return raw
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class AgentConfiguration:
    alias: str
    ssh_config: Path
    installation_id: str
    node_name: str
    revision: str


def agent_configuration(host_id, node_name):
    try:
        source = os.environ.get("RANGE42_PREPARATION_AGENTS_FILE")
        if not source:
            raise ValueError()
        raw = protected_bytes(Path(source))
        value = json.loads(raw, object_pairs_hook=unique_keys)
        if (
            not isinstance(value, dict)
            or type(value.get("version")) is not int
            or value["version"] not in {1, 2}
            or set(value)
            != (
                {"version", "installation_id", "ssh_config", "agents"}
                if value["version"] == 1
                else {"version", "installation_id", "agents"}
            )
            or not isinstance(value["installation_id"], str)
            or not re.fullmatch(r"[0-9a-f]{32}", value["installation_id"])
            or (value["version"] == 1 and not isinstance(value["ssh_config"], str))
            or not isinstance(value["agents"], list)
            or not 1 <= len(value["agents"]) <= 64
        ):
            raise ValueError()
        per_node = value["version"] == 2
        selected = None
        hosts = set()
        for row in value["agents"]:
            if (
                not isinstance(row, dict)
                or set(row)
                != (
                    {"target_host_id", "node_name", "ssh_alias", "ssh_config"}
                    if per_node
                    else {"target_host_id", "node_name", "ssh_alias"}
                )
                or any(not isinstance(row[key], str) for key in row)
                or not re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", row["target_host_id"]
                )
                or row["target_host_id"] in hosts
                or not re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9.-]{0,127}", row["node_name"]
                )
                or not re.fullmatch(
                    r"range42-preparation-[A-Za-z0-9][A-Za-z0-9_-]{0,63}",
                    row["ssh_alias"],
                )
            ):
                raise ValueError()
            hosts.add(row["target_host_id"])
            if row["target_host_id"] == host_id and row["node_name"] == node_name:
                selected = row
        if selected is None:
            raise ValueError()
        ssh_config = Path(selected["ssh_config"] if per_node else value["ssh_config"])
        ssh_digest = hashlib.sha256(protected_bytes(ssh_config)).hexdigest()
        # Existing v1 bindings retain their exact digest. In v2, unrelated node
        # enrollment/reordering must not invalidate an already accepted hold.
        reviewed = (
            {
                "version": 2,
                "installation_id": value["installation_id"],
                "agent": selected,
            }
            if per_node
            else value
        )
        return AgentConfiguration(
            selected["ssh_alias"],
            ssh_config,
            value["installation_id"],
            node_name,
            digest([reviewed, ssh_digest]),
        )
    except (OSError, ValueError, TypeError):
        raise unavailable() from None


async def read_agent(
    config, *, action="inspect", maximum=LIMIT, timeout=15, request=None
):
    if (action, maximum, timeout) not in [
        ("inspect", LIMIT, 15),
        ("capacity", 4194304, 110),
        ("capacity-admit", LIMIT, 110),
        ("capacity-status", LIMIT, 15),
    ]:
        raise unavailable()
    payload = {"action": action}
    if action in {"capacity-admit", "capacity-status"}:
        if not isinstance(request, dict):
            raise unavailable()
        payload["request"] = request
    elif request is not None:
        raise unavailable()
    raw_request = json.dumps(payload).encode() + b"\n"
    if len(raw_request) > 65536:
        raise unavailable()
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            "ssh",
            "-F",
            str(config.ssh_config),
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "StrictHostKeyChecking=yes",
            config.alias,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        async with asyncio.timeout(timeout):
            process.stdin.write(raw_request)
            await process.stdin.drain()
            process.stdin.close()
            raw = b""
            while len(raw) <= maximum:
                chunk = await process.stdout.read(min(65536, maximum + 1 - len(raw)))
                if not chunk:
                    break
                raw += chunk
            if len(raw) > maximum or await process.wait() != 0:
                raise ValueError()
        value = json.loads(raw, object_pairs_hook=unique_keys)
        if (
            not isinstance(value, dict)
            or set(value) != {"ok", "result"}
            or value["ok"] is not True
        ):
            raise ValueError()
        return value["result"]
    except (TimeoutError, OSError, ValueError, TypeError, RuntimeError):
        raise unavailable() from None
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            while await process.stdout.read(65536):
                pass
            await process.wait()


async def inspect_agent(config):
    try:
        result = NodeAttestation.model_validate(await read_agent(config))
        if (result.installation_id, result.node_name) != (
            config.installation_id,
            config.node_name,
        ):
            raise ValueError()
        return result
    except (ValueError, TypeError):
        raise unavailable() from None


def peer_certificate_digest(response):
    try:
        connection = response.extensions["network_stream"].get_extra_info("ssl_object")
        certificate = connection.getpeercert(binary_form=True)
        if not isinstance(certificate, bytes) or not certificate:
            raise ValueError()
        return hashlib.sha256(certificate).hexdigest()
    except (AttributeError, KeyError, TypeError):
        raise ValueError(
            "Actual verified HTTPS peer certificate is unavailable"
        ) from None


async def verify_api_peer(host, attestation):
    try:
        if not host.api_url.startswith("https://"):
            raise ValueError()
        async with httpx.AsyncClient(
            verify=proxmox_verify(), timeout=8, follow_redirects=False
        ) as client:
            response = await client.get(
                host.api_url.rstrip("/") + "/api2/json/version",
                headers={"Authorization": "PVEAPIToken=" + host.token_ref},
            )
            if response.status_code != 200:
                raise ValueError()
            envelope = response.json()
            if not isinstance(envelope, dict):
                raise ValueError()
            data = envelope.get("data")
            if (
                not isinstance(data, dict)
                or not isinstance(data.get("version"), str)
                or not data["version"]
                or peer_certificate_digest(response)
                != attestation.api_certificate_sha256
            ):
                raise ValueError()
            # A proxy/wildcard leaf may be shared. Bind the selected logical
            # node using its generated PVE certificate, independently of the
            # HTTPS transport leaf. This endpoint is read-only/authenticated.
            response = await client.get(
                host.api_url.rstrip("/")
                + "/api2/json/nodes/"
                + quote(attestation.node_name, safe="")
                + "/certificates/info",
                headers={"Authorization": "PVEAPIToken=" + host.token_ref},
            )
            if (
                response.status_code != 200
                or peer_certificate_digest(response)
                != attestation.api_certificate_sha256
            ):
                raise ValueError()
            envelope = response.json()
            rows = envelope.get("data") if isinstance(envelope, dict) else None
            if (
                not isinstance(rows, list)
                or len(rows) > 16
                or any(not isinstance(row, dict) for row in rows)
            ):
                raise ValueError()
            generated = [row for row in rows if row.get("filename") == "pve-ssl.pem"]
            if len(generated) != 1:
                raise ValueError()
            fingerprint = generated[0].get("fingerprint")
            if (
                not isinstance(fingerprint, str)
                or not re.fullmatch(
                    r"(?:[0-9a-fA-F]{64}|(?:[0-9a-fA-F]{2}:){31}[0-9a-fA-F]{2})",
                    fingerprint,
                )
                or fingerprint.replace(":", "").lower()
                != attestation.node_certificate_sha256
            ):
                raise ValueError()
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        raise Range42Error(
            status=409,
            code="PHYSICAL_NODE_ENDPOINT_MISMATCH",
            message="The selected verified HTTPS API does not match the enrolled physical-node inspector. Check the target, credential and node certificate before reviewing again.",
        ) from None
    except RuntimeError:
        raise unavailable() from None
