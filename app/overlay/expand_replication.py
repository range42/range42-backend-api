"""expand_replication — one Ansible play per team, play-level handler rename.

Plan A skeleton: trivial path only.
"""
from copy import deepcopy
from typing import Any, TypedDict

from app.overlay.errors import NotImplementedOperator


class ExpandResult(TypedDict):
    plays_per_team: int
    handler_namespaces: list[str]
    document: dict[str, Any]


def expand_replication(document: dict[str, Any], team_count: int) -> ExpandResult:
    if team_count < 1:
        raise ValueError(f"invalid team_count: {team_count}")

    if _contains_per_team(document.get("nodes", []) or []):
        raise NotImplementedOperator(
            "expand_replication", "per-team-groups", "Plan B delivery"
        )

    return {
        "plays_per_team": team_count,
        "handler_namespaces": [],
        "document": deepcopy(document),
    }


def _contains_per_team(nodes: list[dict[str, Any]]) -> bool:
    for n in nodes:
        repl = n.get("replication") or {}
        if repl.get("scope") == "per_team":
            return True
        if _contains_per_team(n.get("children", []) or []):
            return True
    return False
