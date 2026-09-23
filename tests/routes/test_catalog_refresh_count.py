"""Refresh entry-count must match the walker's manifest recognition.

The refresh endpoint reports ``entries_indexed`` after shallow-cloning each
repo. That count must agree with what ``/v1/catalog/entries`` would surface,
i.e. it must recognise all three manifest formats (range42.yaml, meta.json,
meta/main.yml) — not just range42.yaml.
"""
from __future__ import annotations

import json

from app.routes.v1.catalog.refresh import _count_manifests


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_count_manifests_recognises_all_three_formats(tmp_path):
    _write(tmp_path / "sub_a" / "range42.yaml", "kind: lab\nname: x\n")
    _write(
        tmp_path / "sub_b" / "meta.json",
        json.dumps({"x_range42": {"exercise": {"id": "exer-1"}}}),
    )
    _write(
        tmp_path / "sub_c" / "meta" / "main.yml",
        "galaxy_info:\n  description: role d\n",
    )

    assert _count_manifests(tmp_path) == 3
