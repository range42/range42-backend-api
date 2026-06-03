"""Entry-detail resolution must recognise all three manifest formats.

The detail endpoint used to only read ``range42.yaml`` at the requested
path, so every container (``meta.json``) or Ansible-role (``meta/main.yml``)
entry that the browse endpoint surfaced 404'd on detail. ``_detail_at_path``
must resolve all three, returning the parsed document alongside the summary
fields.
"""
from __future__ import annotations

import json

from app.routes.v1.catalog.entries import _detail_at_path


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_detail_resolves_range42_yaml(tmp_path):
    _write(tmp_path / "sub_a" / "range42.yaml", "kind: lab\nname: x\n")
    d = _detail_at_path(tmp_path, "sub_a")
    assert d is not None
    assert d["kind"] == "lab"
    assert d["name"] == "x"
    assert d["document"]["kind"] == "lab"


def test_detail_resolves_meta_json_container(tmp_path):
    _write(
        tmp_path / "sub_b" / "meta.json",
        json.dumps(
            {
                "x_range42": {
                    "exercise": {"id": "exer-1"},
                    "catalog": {"tags": ["t1"]},
                    "vuln": {"title": "vuln title"},
                }
            }
        ),
    )
    d = _detail_at_path(tmp_path, "sub_b")
    assert d is not None
    assert d["kind"] == "container"
    assert d["name"] == "exer-1"
    assert d["description"] == "vuln title"
    assert d["tags"] == ["t1"]
    assert d["document"]["x_range42"]["exercise"]["id"] == "exer-1"


def test_detail_resolves_meta_main_yml_role(tmp_path):
    _write(
        tmp_path / "sub_c" / "meta" / "main.yml",
        "galaxy_info:\n  description: role d\n  galaxy_tags:\n    - g1\n",
    )
    d = _detail_at_path(tmp_path, "sub_c")
    assert d is not None
    assert d["kind"] == "ansible_role"
    assert d["name"] == "sub_c"
    assert d["description"] == "role d"
    assert d["tags"] == ["g1"]
    assert d["document"]["galaxy_info"]["description"] == "role d"


def test_detail_missing_path_returns_none(tmp_path):
    _write(tmp_path / "sub_a" / "range42.yaml", "kind: lab\nname: x\n")
    assert _detail_at_path(tmp_path, "nope") is None
