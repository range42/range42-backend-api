"""Small read-only API boundary shared by concrete scenario checks."""
from __future__ import annotations

import httpx

from app.core.models import ProxmoxHost


class ProxmoxReadError(Exception):
    """A safe error that excludes credentials and upstream response bodies."""


async def read_proxmox_data(client: httpx.AsyncClient, host: ProxmoxHost,
                            path: str, *, params: dict | None = None) -> dict | list | str | int:
    try:
        response = await client.get(f"{host.api_url.rstrip('/')}/api2/json{path}", params=params,
                                    headers={"Authorization": f"PVEAPIToken={host.token_ref}"})
        response.raise_for_status()
        data = response.json()["data"]
        if type(data) not in (dict, list, str, int):
            raise ValueError("unexpected response shape")
        return data
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise ProxmoxReadError(f"Cannot read {path} on the selected Proxmox host. Check its API permissions and connectivity.") from exc


async def list_proxmox_data(client: httpx.AsyncClient, host: ProxmoxHost,
                            path: str, *, params: dict | None = None) -> list[dict]:
    data = await read_proxmox_data(client, host, path, params=params)
    if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
        raise ProxmoxReadError(f"Unexpected response from {path}.")
    return data
