"""Catalog walker tests for the three manifest formats.

Exercises ``_discover()`` directly against a fake on-disk catalog tree
(no git clone, no FastAPI). Covers:

* Recognition of ``range42.yaml``, ``meta.json``, and ``meta/main.yml``.
* Resilience to malformed ``meta.json`` and ``meta/main.yml`` (warn + skip,
  do not raise; other valid entries still surface).
"""
from __future__ import annotations

import json

from app.routes.v1.catalog.entries import _discover


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_discover_finds_range42_yaml_meta_json_and_meta_main_yml(tmp_path):
    _write(tmp_path / "sub_a" / "range42.yaml", "kind: lab\nname: x\n")
    _write(
        tmp_path / "sub_b" / "meta.json",
        json.dumps(
            {
                "x_range42": {
                    "exercise": {"id": "exer-1"},
                    "catalog": {"tags": ["t1", "t2"]},
                    "vuln": {"title": "vuln title"},
                }
            }
        ),
    )
    _write(
        tmp_path / "sub_c" / "meta" / "main.yml",
        "galaxy_info:\n  description: role d\n  galaxy_tags:\n    - g1\n    - g2\n",
    )

    entries = _discover(tmp_path)
    by_kind = {e["kind"]: e for e in entries}

    assert set(by_kind) == {"lab", "container", "ansible_role"}

    lab = by_kind["lab"]
    assert lab["path"] == "sub_a"
    assert lab["name"] == "x"
    assert lab["tags"] == []

    container = by_kind["container"]
    assert container["path"] == "sub_b"
    assert container["name"] == "exer-1"
    assert container["description"] == "vuln title"
    assert container["tags"] == ["t1", "t2"]

    role = by_kind["ansible_role"]
    assert role["path"] == "sub_c"
    assert role["name"] == "sub_c"
    assert role["description"] == "role d"
    assert role["tags"] == ["g1", "g2"]


def test_discover_skips_malformed_meta_json(tmp_path):
    _write(tmp_path / "good" / "range42.yaml", "kind: lab\nname: ok\n")
    _write(tmp_path / "bad" / "meta.json", "{not valid json")

    entries = _discover(tmp_path)
    kinds = {e["kind"] for e in entries}

    assert "lab" in kinds
    # malformed meta.json must NOT raise and must NOT surface as an entry
    assert all(e["path"] != "bad" for e in entries)


def test_discover_skips_malformed_meta_main_yml(tmp_path):
    _write(tmp_path / "good" / "range42.yaml", "kind: lab\nname: ok\n")
    _write(
        tmp_path / "bad_role" / "meta" / "main.yml",
        "galaxy_info: [this: is: not: valid yaml\n",
    )

    entries = _discover(tmp_path)

    assert any(e["kind"] == "lab" for e in entries)
    # malformed meta/main.yml must NOT raise and must NOT surface as an entry
    assert all(e["path"] != "bad_role" for e in entries)
