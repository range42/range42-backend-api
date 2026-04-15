"""resolve_secrets — substitute env/secret references against a vault map.

Plan A skeleton: identity when vault is empty OR doc has no env;
otherwise NotImplementedOperator (Plan B).
"""
from copy import deepcopy
from typing import Any, Mapping

from app.overlay.errors import NotImplementedOperator


def resolve_secrets(
    document: dict[str, Any], vault: Mapping[str, str]
) -> dict[str, Any]:
    has_env = bool(document.get("env"))
    has_vault = bool(vault)
    if has_env and has_vault:
        raise NotImplementedOperator(
            "resolve_secrets", "env-substitution", "Plan B delivery"
        )
    return deepcopy(document)
