"""Redaction pipeline.

Four-layer interface:
  1. SaveTimeLint        -- stretch, stub
  2. ConfigDenylist      -- hard invariant, implemented in Task 14
  3. VaultTagged         -- hard invariant, implemented in Task 15
  4. ContentRegex        -- stretch, stub

Pipeline orchestration lives in `run_pipeline()`. Layers run in order,
never short-circuit -- every fired layer appends one audit row.
"""
from __future__ import annotations

import fnmatch
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


class RedactionLayer(Protocol):
    name: str

    def redact(self, event: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
        """Return (new_event, [{'rule_id', 'field_path'}, ...])."""
        ...


class RedactionAuditWriter:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        *,
        deployment_id: str,
        attempt_id: str,
        layer: str,
        rule_id: str,
        event_seq: int,
        field_path: str,
    ) -> None:
        row = {
            "ts": datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "deployment_id": deployment_id,
            "attempt_id": attempt_id,
            "layer": layer,
            "rule_id": rule_id,
            "event_seq": event_seq,
            "field_path": field_path,
        }
        with self.path.open("ab") as fh:
            fh.write(json.dumps(row, separators=(",", ":")).encode("utf-8") + b"\n")


def run_pipeline(
    event: dict[str, Any],
    layers: list[RedactionLayer],
    *,
    audit: RedactionAuditWriter,
    deployment_id: str,
    attempt_id: str,
) -> dict[str, Any]:
    """Run all layers in order. Never short-circuits; every fired layer
    appends one audit row per field touched."""
    current = event
    for layer in layers:
        current, fired = layer.redact(current)
        for f in fired:
            audit.record(
                deployment_id=deployment_id,
                attempt_id=attempt_id,
                layer=layer.name,
                rule_id=f["rule_id"],
                event_seq=int(current.get("event_seq") or 0),
                field_path=f["field_path"],
            )
    return current


class ConfigDenylistLayer:
    """Layer 2 (hard invariant). Redacts any dict key whose name matches
    one of the configured fnmatch patterns, at any depth. Also handles the
    structural `{name, value, secret: true}` env shape emitted by the
    deployment plan extractor."""

    name = "config_denylist"

    def __init__(self, patterns: tuple[str, ...]) -> None:
        self.patterns = patterns

    def _matches(self, key: str) -> bool:
        return any(fnmatch.fnmatchcase(key, pat) for pat in self.patterns)

    def redact(self, event: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
        fired: list[dict[str, str]] = []
        new = deepcopy(event)

        def walk(obj: Any, path: str) -> None:
            if isinstance(obj, dict):
                # env.secret=true handling
                if obj.get("secret") is True and "value" in obj:
                    obj["value"] = "[REDACTED:config_denylist]"
                    fired.append(
                        {
                            "rule_id": "denylist:env.secret-true",
                            "field_path": f"{path}.value" if path else "value",
                        }
                    )
                for k, v in list(obj.items()):
                    sub = f"{path}.{k}" if path else k
                    if isinstance(v, (dict, list)):
                        walk(v, sub)
                    elif self._matches(k):
                        obj[k] = "[REDACTED:config_denylist]"
                        fired.append({"rule_id": f"denylist:{k}", "field_path": sub})
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    walk(item, f"{path}[{i}]")

        walk(new, "")
        return new, fired


class SaveTimeLintLayer:
    """Layer 1 (stretch, deferred per spec §13). Interface reserved so the
    pipeline supports all four layers from day one."""

    name = "save_time_lint"

    def redact(self, event: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
        raise NotImplementedError("save_time_lint deferred per spec §13")


class ContentRegexLayer:
    """Layer 4 (stretch, deferred per spec §13). Accepts a list of
    (rule_id, regex) tuples for future activation."""

    name = "content_regex"

    def __init__(self, rules: tuple[tuple[str, str], ...] = ()) -> None:
        self.rules = rules

    def redact(self, event: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
        raise NotImplementedError("content_regex deferred per spec §13")


class TaintedStringLayer:
    """Substring-replace known-secret values in event content fields.

    Used to catch secrets that leak via stdout/stderr/msg/debug regardless
    of which key carried them. Build the tainted set at attempt start from
    cloudinit vars, vault password, and Source PAT.

    Unlike ConfigDenylistLayer (matches by key name), this layer scans
    the *content* of free-text fields and substring-replaces any known
    tainted value. Targeted fields are stdout/stderr/msg (strings) and
    stdout_lines/stderr_lines (lists of strings).
    """

    name = "tainted_string"

    # Fields where free-text content may contain secret values
    _SCAN_FIELDS = ("stdout", "stderr", "msg")
    _SCAN_LINES_FIELDS = ("stdout_lines", "stderr_lines")

    def __init__(self, tainted_strings: set[str]) -> None:
        # Filter empty strings (would match everywhere) — sort by length
        # descending so longer secrets are replaced before any shorter
        # substrings of them, avoiding partial-redaction artefacts.
        self._tainted = tuple(
            sorted((t for t in tainted_strings if t), key=len, reverse=True)
        )

    def redact(self, event: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
        if not self._tainted:
            return event, []
        fired: list[dict[str, str]] = []
        new = deepcopy(event)
        self._walk(new, "", fired)
        return new, fired

    def _walk(self, obj: Any, path: str, fired: list[dict[str, str]]) -> None:
        if isinstance(obj, dict):
            for k, v in list(obj.items()):
                sub = f"{path}.{k}" if path else k
                if k in self._SCAN_FIELDS and isinstance(v, str):
                    obj[k] = self._redact_string(v, sub, fired)
                elif k in self._SCAN_LINES_FIELDS and isinstance(v, list):
                    obj[k] = [
                        self._redact_string(s, f"{sub}[{i}]", fired)
                        if isinstance(s, str)
                        else s
                        for i, s in enumerate(v)
                    ]
                elif isinstance(v, (dict, list)):
                    self._walk(v, sub, fired)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                self._walk(item, f"{path}[{i}]", fired)

    def _redact_string(self, s: str, field_path: str, fired: list[dict[str, str]]) -> str:
        out = s
        for t in self._tainted:
            if t and t in out:
                out = out.replace(t, "[REDACTED:tainted_string]")
                # Audit rule_id includes a short prefix for traceability
                # without leaking the full secret.
                prefix = t[:4] if len(t) > 4 else "***"
                fired.append(
                    {
                        "rule_id": f"tainted_string:{prefix}***",
                        "field_path": field_path,
                    }
                )
        return out


VAULT_MARKER = "__range42_vault_origin__"


class VaultTaggedLayer:
    """Layer 3 (hard invariant). Redacts any value that was resolved via
    the vault resolver (carries ``__range42_vault_origin__`` marker) or
    any raw string whose lstrip starts with ``!vault`` (leaked inline
    YAML vault tag)."""

    name = "vault_tagged"

    def redact(self, event: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
        fired: list[dict[str, str]] = []
        new = deepcopy(event)

        def walk(obj: Any, path: str) -> tuple[Any, bool, str]:
            if isinstance(obj, dict) and obj.get(VAULT_MARKER) is True:
                return "[REDACTED:vault_tagged]", True, "vault:tagged-value"
            if isinstance(obj, str) and obj.lstrip().startswith("!vault"):
                return "[REDACTED:vault_tagged]", True, "vault:ansible-vault-inline"
            if isinstance(obj, dict):
                for k, v in list(obj.items()):
                    sub = f"{path}.{k}" if path else k
                    replaced, hit, rule = walk(v, sub)
                    if hit:
                        obj[k] = replaced
                        fired.append({"rule_id": rule, "field_path": sub})
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    sub = f"{path}[{i}]"
                    replaced, hit, rule = walk(item, sub)
                    if hit:
                        obj[i] = replaced
                        fired.append({"rule_id": rule, "field_path": sub})
            return obj, False, ""

        walk(new, "")
        return new, fired
