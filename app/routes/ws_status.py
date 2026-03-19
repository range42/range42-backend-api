"""
WebSocket endpoint for real-time VM status updates.

Polls Proxmox API directly via httpx (not Ansible — too slow for real-time)
and pushes status changes to connected clients.

Usage:
    ws://host:8000/ws/vm-status?node=pve01&api_host=100.64.0.14:8006&token_id=root@pam!range42-deploy&token_secret=xxx
"""

import asyncio
import json
import logging
from typing import Dict, Optional

import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter()

POLL_INTERVAL = 5  # seconds


async def fetch_vm_status(
    client: httpx.AsyncClient,
    api_host: str,
    node: str,
    token_id: str,
    token_secret: str,
) -> list[dict]:
    """Fetch VM list directly from Proxmox API (bypasses Ansible for speed)."""
    url = f"https://{api_host}/api2/json/nodes/{node}/qemu"
    headers = {"Authorization": f"PVEAPIToken={token_id}={token_secret}"}

    try:
        resp = await client.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        return [
            {
                "vmid": vm["vmid"],
                "name": vm.get("name", ""),
                "status": vm.get("status", "unknown"),
                "cpu": round(vm.get("cpu", 0) * 100, 1),
                "mem": vm.get("mem", 0),
                "maxmem": vm.get("maxmem", 0),
                "uptime": vm.get("uptime", 0),
                "template": vm.get("template", 0),
                "tags": vm.get("tags", ""),
            }
            for vm in data
        ]
    except Exception as e:
        logger.warning(f"[ws] Proxmox poll failed: {e}")
        return []


def compute_diff(
    prev: Dict[int, dict], current: Dict[int, dict]
) -> Optional[dict]:
    """Compare previous and current VM states, return changes."""
    changes = {}

    for vmid, vm in current.items():
        old = prev.get(vmid)
        if old is None:
            changes[vmid] = {"type": "added", **vm}
        elif old["status"] != vm["status"] or abs(old.get("cpu", 0) - vm.get("cpu", 0)) > 2:
            changes[vmid] = {"type": "changed", **vm}

    for vmid in prev:
        if vmid not in current:
            changes[vmid] = {"type": "removed", "vmid": vmid}

    return changes if changes else None


@router.websocket("/ws/vm-status")
async def vm_status_websocket(ws: WebSocket):
    await ws.accept()

    # Read connection params from query string
    params = ws.query_params
    node = params.get("node", "pve01")
    api_host = params.get("api_host", "")
    token_id = params.get("token_id", "")
    token_secret = params.get("token_secret", "")

    if not api_host or not token_id or not token_secret:
        await ws.send_json({"error": "Missing api_host, token_id, or token_secret query params"})
        await ws.close()
        return

    logger.info(f"[ws] Client connected for node={node} via {api_host}")

    prev_state: Dict[int, dict] = {}

    async with httpx.AsyncClient(verify=False) as client:
        try:
            while True:
                vms = await fetch_vm_status(client, api_host, node, token_id, token_secret)

                current_state = {vm["vmid"]: vm for vm in vms if vm.get("template", 0) != 1}

                # First message: send full state
                if not prev_state:
                    await ws.send_json({
                        "type": "full",
                        "vms": list(current_state.values()),
                    })
                else:
                    # Subsequent: send only changes
                    diff = compute_diff(prev_state, current_state)
                    if diff:
                        await ws.send_json({
                            "type": "diff",
                            "changes": diff,
                        })

                prev_state = current_state
                await asyncio.sleep(POLL_INTERVAL)

        except WebSocketDisconnect:
            logger.info(f"[ws] Client disconnected for node={node}")
        except Exception as e:
            logger.error(f"[ws] Error: {e}")
            try:
                await ws.send_json({"error": str(e)})
            except Exception:
                pass
