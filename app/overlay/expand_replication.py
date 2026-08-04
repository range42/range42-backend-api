"""Deterministic expansion: one play per team.

For every top-level node with replication.scope == 'per_team' (and each
group's children), emit N copies with per-team offsets applied to
vmid, ip, bridge, vlan, hostname, flag values, env scope=per_team.

Handler name rewrite: play-level notify: '<name>' -> '<name>__team_<id>'
keeping role-internal handlers untouched (per-team play boundary
isolates them naturally).

Returns an ExpandResult dict with:
  plays_per_team: number of per-team plays emitted (== team_count)
  handler_namespaces: sorted list of per-team handler namespace tokens
  document: the expanded document
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, TypedDict


class ExpandResult(TypedDict):
    plays_per_team: int
    handler_namespaces: list[str]
    document: dict[str, Any]


# Canonical schema form: Jinja-ish `{{ bridge_base + team_id }}`.
_JINJA_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
# Legacy single-brace numeric form: `{140+team_id}` (still accepted).
_TEMPLATE_RE = re.compile(r"\{(\d*)\s*([+\-*])?\s*team_id\s*\}")
_TOKEN_RE = re.compile(r"\d+|team_id|bridge_base|[+\-*]")

DEFAULT_BRIDGE_BASE = 140

# Network templates live at node level per the canonical schema
# (network kind only).
_NODE_TEMPLATES = (
    ("cidr_template", "cidr"),
    ("bridge_template", "bridge"),
    ("gateway_template", "gateway"),
)


def _eval_expr(expr: str, team_id: int, bridge_base: int) -> str:
    """Left-to-right integer expression over team_id/bridge_base with + - *.

    No operator precedence — kept simple for TS parity.
    """
    tokens = _TOKEN_RE.findall(expr)
    if not tokens:
        return expr

    def val(tok: str) -> int:
        if tok == "team_id":
            return team_id
        if tok == "bridge_base":
            return bridge_base
        return int(tok)

    acc = val(tokens[0])
    for i in range(1, len(tokens) - 1, 2):
        op = tokens[i]
        operand = val(tokens[i + 1])
        if op == "+":
            acc += operand
        elif op == "-":
            acc -= operand
        elif op == "*":
            acc *= operand
    return str(acc)


def _render_template(tpl: str, team_id: int,
                     bridge_base: int = DEFAULT_BRIDGE_BASE) -> str:
    def jinja(m: re.Match[str]) -> str:
        return _eval_expr(m.group(1), team_id, bridge_base)

    def legacy(m: re.Match[str]) -> str:
        base = int(m.group(1) or 0)
        op = m.group(2) or "+"
        if op == "+":
            return str(base + team_id)
        if op == "-":
            return str(base - team_id)
        return str(base * team_id)

    return _TEMPLATE_RE.sub(legacy, _JINJA_RE.sub(jinja, tpl))


def _apply_offsets(node: dict, team_id: int,
                   id_offset: dict | None,
                   namespace_sink: list[str],
                   bridge_base: int) -> dict:
    out = deepcopy(node)
    out["id"] = f"{node['id']}__team_{team_id}"
    cfg = out.get("config") or {}
    if "name_template" in cfg:
        cfg["name"] = _render_template(
            cfg.pop("name_template"), team_id, bridge_base)
    if "vlan_template" in cfg:
        cfg["vlan"] = int(_render_template(
            cfg.pop("vlan_template"), team_id, bridge_base))
    if id_offset and "vmid" in id_offset and "vm_id" in cfg:
        cfg["vm_id"] = int(cfg["vm_id"]) + id_offset["vmid"] * team_id
    out["config"] = cfg
    for tkey, okey in _NODE_TEMPLATES:
        if isinstance(out.get(tkey), str):
            out[okey] = _render_template(out.pop(tkey), team_id, bridge_base)
    if isinstance(out.get("networks"), list):
        for nw in out["networks"]:
            if "ip_template" in nw:
                nw["ip"] = _render_template(
                    nw.pop("ip_template"), team_id, bridge_base)
    # Rewrite play-level notify targets inside attachments.
    for att in out.get("attachments") or []:
        if isinstance(att.get("notify"), list):
            att["notify"] = [f"{n}__team_{team_id}" for n in att["notify"]]
        elif isinstance(att.get("notify"), str):
            att["notify"] = f"{att['notify']}__team_{team_id}"
        if att.get("ansible_primitive") == "handler":
            base_ns = att.get("handler_namespace") or ""
            ns = f"{base_ns}__team_{team_id}" if base_ns else f"team_{team_id}"
            att["handler_namespace"] = ns
            namespace_sink.append(ns)
    return out


def _walk_and_expand(nodes: list[dict], team_count: int,
                     namespace_sink: list[str],
                     bridge_base: int) -> list[dict]:
    result: list[dict] = []
    for n in nodes:
        rep = n.get("replication") or {}
        scope = rep.get("scope", "shared")
        if scope == "shared":
            if n.get("kind") == "group" and isinstance(n.get("children"), list):
                nn = deepcopy(n)
                nn["children"] = _walk_and_expand(
                    n["children"], team_count, namespace_sink, bridge_base)
                result.append(nn)
            else:
                result.append(deepcopy(n))
            continue
        # per_team
        id_offset = rep.get("id_offset") or {}
        for tid in range(1, team_count + 1):
            if n.get("kind") == "group" and isinstance(n.get("children"), list):
                expanded_children = [
                    _apply_offsets(c, tid, id_offset, namespace_sink,
                                   bridge_base)
                    for c in n["children"]
                ]
                grp = deepcopy(n)
                grp["id"] = f"{n['id']}__team_{tid}"
                grp["children"] = expanded_children
                grp["replication"] = {"scope": "shared"}
                result.append(grp)
            else:
                result.append(_apply_offsets(n, tid, id_offset,
                                             namespace_sink, bridge_base))
    return result


def expand_replication(doc: dict, team_count: int) -> ExpandResult:
    if team_count < 1:
        raise ValueError(f"invalid team_count: {team_count}")
    out = deepcopy(doc)
    namespace_sink: list[str] = []
    raw_base = doc.get("bridge_base")
    bridge_base = raw_base if isinstance(raw_base, int) and not isinstance(
        raw_base, bool) else DEFAULT_BRIDGE_BASE
    out["nodes"] = _walk_and_expand(
        doc.get("nodes", []), team_count, namespace_sink, bridge_base)
    return {
        "plays_per_team": team_count,
        "handler_namespaces": namespace_sink,
        "document": out,
    }
