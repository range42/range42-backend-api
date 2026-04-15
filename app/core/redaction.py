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

import json
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
