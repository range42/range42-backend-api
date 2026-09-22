"""Bind reviewed operations to exact target, revision, runtime and affected state."""
import hashlib
import json

from app.core.errors import Range42Error
from app.core.runtime_operations import blocked

ADMIN_OPERATIONS = frozenset({"host_firewall", "sdn_network", "firewall_alias", "firewall_rule"})
REVIEWED_OPERATIONS = ADMIN_OPERATIONS


def authorize_operation(kind, principal):
    if kind in ADMIN_OPERATIONS and (principal is None or principal.role != "admin"):
        raise Range42Error(status=403, code="RUNTIME_ADMIN_REQUIRED", error="forbidden",
                           message="A backend administrator must review and authorize this shared operation")


def review_fingerprint(operation, plan):
    request = {key: value for key, value in operation["request"].items() if key != "review_fingerprint"}
    binding = {key: operation[key] for key in ("project_sha", "target_host_id", "target_identity", "runtime")}
    return hashlib.sha256(json.dumps({**binding, "request": request, "plan": plan}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def verify_review(operation, plan):
    if operation["request"]["kind"] not in REVIEWED_OPERATIONS:
        return
    if operation.get("authorized_role") != "admin":
        raise blocked("Administrator authorization is missing from this queued operation", "RUNTIME_ADMIN_REQUIRED")
    expected = operation["request"].get("review_fingerprint")
    if not expected:
        raise blocked("Review this operation's current target and affected resources before applying", "RUNTIME_REVIEW_REQUIRED")
    if expected != review_fingerprint(operation, plan):
        raise blocked("The target or affected state changed after review. Refresh the plan before applying", "RUNTIME_REVIEW_CHANGED")
