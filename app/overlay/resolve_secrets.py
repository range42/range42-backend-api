"""Resolve 'env.secret.<name>' references in attachment vars into
marker-wrapped values for downstream redaction.

Pure transform; caller supplies a SecretLookup (vault-backed in prod,
dict-backed in tests).

Spec refs: §3 operator chain resolve_secrets, §7 redaction layer 3.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Protocol

from app.core.redaction import VAULT_MARKER


class SecretLookup(Protocol):
    def get(self, name: str) -> str | None: ...


def _walk(obj, lookup: SecretLookup) -> None:
    if isinstance(obj, dict):
        # Rewrite any *_from: "env.secret.<name>" -> key without _from,
        # wrapped with the vault origin marker.
        rewrites: list[tuple[str, str]] = []
        for k, v in list(obj.items()):
            if isinstance(v, str) and k.endswith("_from") and v.startswith("env.secret."):
                name = v[len("env.secret."):]
                val = lookup.get(name)
                if val is None:
                    raise KeyError(f"Secret not found: {name}")
                dst = k[: -len("_from")]
                rewrites.append((k, dst))
                obj[dst] = {VAULT_MARKER: True, "value": val}
        for k, _ in rewrites:
            obj.pop(k, None)
        for v in obj.values():
            _walk(v, lookup)
    elif isinstance(obj, list):
        for item in obj:
            _walk(item, lookup)


def resolve_secrets(document: dict, lookup: SecretLookup) -> dict:
    out = deepcopy(document)
    _walk(out, lookup)
    return out
