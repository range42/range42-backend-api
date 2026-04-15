"""compose(base, overlay) -> effective catalog document.

Plan A skeleton: identity-only. Structural overlay edits raise
NotImplementedOperator (Plan B).
"""
from copy import deepcopy
from typing import Any

from app.overlay.errors import NotImplementedOperator


def compose(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    structural_keys = (
        "nodes_added", "nodes_removed", "nodes_patched",
        "attachments_added", "execution_override",
    )
    has_edits = any(overlay.get(k) for k in structural_keys)
    param_overrides = overlay.get("param_overrides") or {}
    if has_edits or param_overrides:
        raise NotImplementedOperator("compose", "structural-edits", "Plan B delivery")
    return deepcopy(base)
