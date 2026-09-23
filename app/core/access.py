"""Named API principals and explicit installation-wide role permissions."""
from dataclasses import dataclass
import json
from pathlib import Path
import re
import stat

from app.core.config import Settings


@dataclass(frozen=True)
class Principal:
    actor_id: str
    role: str


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate principal configuration field")
        result[key] = value
    return result


def configured_principals(settings: Settings) -> dict[str, Principal]:
    if not settings.api_principals_file:
        return {}
    try:
        path = Path(settings.api_principals_file)
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 262144:
            raise ValueError()
        value = json.loads(path.read_text(), object_pairs_hook=_unique_keys)
        if not isinstance(value, dict) or set(value) != {"version", "principals"} or type(value["version"]) is not int or value["version"] != 1:
            raise ValueError()
        rows = value["principals"]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
            raise ValueError()
        identities, result = set(), {}
        for row in rows:
            if (not isinstance(row, dict) or set(row) != {"id", "role", "token_sha256"}
                    or not isinstance(row["id"], str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}", row["id"])
                    or row["id"] in {"shared-operator", "development"}
                    or row["role"] not in {"admin", "operator", "viewer"}
                    or not isinstance(row["token_sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", row["token_sha256"])
                    or row["id"] in identities or row["token_sha256"] in result):
                raise ValueError()
            identities.add(row["id"])
            result[row["token_sha256"]] = Principal(row["id"], row["role"])
        if not any(row.role == "admin" for row in result.values()) and not (settings.api_token or settings.api_token_file):
            raise ValueError()
        return result
    except (OSError, ValueError, TypeError, KeyError):
        raise RuntimeError("RANGE42_API_PRINCIPALS_FILE must be a private, bounded version1 principal file with unique identities, SHA256 token hashes and valid roles; an administrator must remain configured") from None


# Route templates, never user-supplied paths. New routes are denied until reviewed.
VIEWER_READS = frozenset({
    "/v1/auth/me", "/v1/health", "/v1/health/ready", "/v1/infra/mirror/health",
    "/v1/catalog/sources", "/v1/catalog/entries", "/v1/catalog/entries/{source_id}/{path}",
    "/v1/projects/", "/v1/projects/{project_id}",
    "/v1/contexts", "/v1/projects/{project_id}/native-scenario",
    "/v1/deployments/", "/v1/deployments/{deployment_id}",
    *[f"/v1/deployments/{{deployment_id}}/{suffix}" for suffix in (
        "allocations", "attempts", "events/download", "events", "preflight", "snapshots", "timings", "runtime", "snapshot-sets")],
    "/v1/deployments/{deployment_id}/snapshot-sets/{set_id}",
    "/v1/proxmox/hosts/{host_id}/sdn/zones", "/v1/proxmox/hosts/{host_id}/sdn/vnets",
    "/v1/proxmox/runtime-capabilities",
    "/v1/proxmox/hosts/{host_id}/sdn/vnets/{vnet}/subnets",
    "/v1/proxmox/hosts", "/v1/proxmox/hosts/{host_id}/health", "/v1/proxmox/hosts/{host_id}/vms",
    "/v1/proxmox/hosts/{host_id}/vms/{vmid}/status", "/v1/proxmox/hosts/{host_id}/tasks/{upid}/status",
    "/v1/proxmox/hosts/{host_id}/storage", "/v1/proxmox/hosts/{host_id}/storage/{store}/content",
    "/v1/proxmox/hosts/{host_id}/capacity", "/v1/proxmox/hosts/{host_id}/vms/{vmid}/snapshots",
})
OPERATOR_READS = frozenset({
    "/v1/proxmox/hosts/{host_id}/vms/{vmid}/hardware/review",
    "/v1/proxmox/hosts/{host_id}/vms/{vmid}/config", "/v1/proxmox/hosts/{host_id}/vms/{vmid}/config/review",
    "/v1/proxmox/hosts/{host_id}/reservations/{reservation_id}", "/v1/admin/retention",
})
OPERATOR_WRITES = frozenset({
    ("PUT", "/v1/proxmox/hosts/{host_id}/vms/{vmid}/hardware/nics/{nic_id}"),
    ("PUT", "/v1/proxmox/hosts/{host_id}/vms/{vmid}/hardware/disks/{disk_id}/grow"),
    ("POST", "/v1/projects/"), ("PUT", "/v1/projects/{project_id}"), ("PATCH", "/v1/projects/{project_id}"),
    *[("POST", f"/v1/projects/{{project_id}}/{suffix}") for suffix in ("heartbeat", "compose", "validate")],
    ("POST", "/v1/catalog/sources/{source_id}/bundles/resolve"),
    ("POST", "/v1/deployments/"), ("DELETE", "/v1/deployments/{deployment_id}"), ("DELETE", "/v1/deployments/{deployment_id}/allocations"),
    *[("POST", f"/v1/deployments/{{deployment_id}}/{suffix}") for suffix in (
        "attempts", "preflight", "teams/{team_id}/reset", "snapshot", "rollback", "cancel", "operations",
        "snapshot-sets/plan", "snapshot-sets/retention/plan", "snapshot-sets/{set_id}/execute", "snapshot-sets/{set_id}/reconcile",
        "snapshot-sets/{set_id}/rollback/plan", "snapshot-sets/{set_id}/delete/plan")],
    ("DELETE", "/v1/deployments/{deployment_id}/snapshot-sets/{set_id}/plans/{operation_id}"),
    ("PUT", "/v1/proxmox/hosts/{host_id}/vms/{vmid}/config"), ("POST", "/v1/proxmox/hosts/{host_id}/vms/{vmid}/status/{action}"),
    ("DELETE", "/v1/proxmox/hosts/{host_id}/vms/{vmid}"), ("POST", "/v1/proxmox/hosts/{host_id}/vms/{vmid}/snapshots"),
    ("DELETE", "/v1/proxmox/hosts/{host_id}/vms/{vmid}/snapshots/{name}"),
    ("POST", "/v1/proxmox/hosts/{host_id}/vms/{vmid}/snapshots/{name}/rollback"),
    ("POST", "/v1/proxmox/hosts/{host_id}/reservations"), ("DELETE", "/v1/proxmox/hosts/{host_id}/reservations/{reservation_id}"),
})


def allowed(principal: Principal, method: str, route: str) -> bool:
    if principal.role == "admin":
        return True
    if method in {"GET", "HEAD"}:
        return route in VIEWER_READS or (principal.role == "operator" and route in OPERATOR_READS)
    return principal.role == "operator" and (method, route) in OPERATOR_WRITES
