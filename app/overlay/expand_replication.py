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


# Character classes are spelled out rather than using \d and \s: Python
# matches all Unicode digits and extra separators where JS matches [0-9]
# and its own whitespace set, which would silently break TS/Python parity.
_WS = r"[ \t\n\r\f\v]"
_DIGIT = r"[0-9]"
# Canonical schema form: Jinja-ish `{{ bridge_base + team_id }}`.
_JINJA_RE = re.compile(rf"\{{\{{{_WS}*([^{{}}]+?){_WS}*\}}\}}")
# Legacy single-brace numeric form: `{140+team_id}` (still accepted).
_TEMPLATE_RE = re.compile(
    rf"\{{({_DIGIT}*){_WS}*([+\-*])?{_WS}*team_id{_WS}*\}}")
_TOKEN_RE = re.compile(rf"{_DIGIT}+|team_id|bridge_base|[+\-*]")
# Only expressions built solely from the supported grammar are rendered;
# anything else (`{{ inventory_hostname }}`, `{{ custom_id + 1 }}`) is a
# plain Ansible template and must survive expansion untouched.
_TERM = rf"(?:{_DIGIT}+|team_id|bridge_base)"
_SUPPORTED_EXPR_RE = re.compile(
    rf"^{_WS}*{_TERM}(?:{_WS}*[+\-*]{_WS}*{_TERM})*{_WS}*$")

DEFAULT_BRIDGE_BASE = 140

# Network templates live at node level per the canonical schema
# (network kind only).
_NODE_TEMPLATES = (
    ("cidr_template", "cidr"),
    ("bridge_template", "bridge"),
    ("gateway_template", "gateway"),
)


def _num_to_str(value: float) -> str:
    """Stringify like JS ``String(n)``: integral floats lose the ``.0``."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


_UNDEFINED = object()


def _js_str(value: object) -> str:
    """Stringify a node id / notify target the way JS template literals do."""
    if value is _UNDEFINED:
        return "undefined"
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, float):
        return _num_to_str(value)
    return str(value)


def _num_to_str_value(v: float) -> float | int:
    """Keep integral results as ints so JSON matches JS number output."""
    return int(v) if isinstance(v, float) and v.is_integer() else v


def _js_number(value: object) -> float | None:
    """Number() semantics: null is 0, booleans are 0/1, junk strings are NaN."""
    if value is None:
        return 0
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            return float(value.strip() or 0)
        except ValueError:
            return None
    return None


def _js_parse_int(value: object) -> int | None:
    """parseInt() semantics: leading integer wins, anything else is NaN.

    JS serializes NaN to null, so that is what the caller stores. Python's
    int() would raise instead, which surfaced as a 500 from the compose
    preview where TS quietly produced null.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str):
        return None
    m = re.match(r"[ \t\n\r\f\v]*([+-]?[0-9]+)", value)
    return int(m.group(1)) if m else None


def _eval_expr(expr: str, team_id: int, bridge_base: float) -> str:
    """Left-to-right numeric expression over team_id/bridge_base with + - *.

    No operator precedence — kept simple for TS parity.
    """
    tokens = _TOKEN_RE.findall(expr)
    if not tokens:
        return expr

    def val(tok: str) -> float:
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
    return _num_to_str(acc)


def _render_template(tpl: str, team_id: int,
                     bridge_base: float = DEFAULT_BRIDGE_BASE) -> str:
    def jinja(m: re.Match[str]) -> str:
        inner = m.group(1)
        if not _SUPPORTED_EXPR_RE.match(inner):
            return m.group(0)
        return _eval_expr(inner, team_id, bridge_base)

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
    raw_id = node["id"] if "id" in node else _UNDEFINED
    out["id"] = f"{_js_str(raw_id)}__team_{team_id}"
    raw_cfg = out.get("config")
    cfg = raw_cfg if isinstance(raw_cfg, dict) else {}
    if isinstance(cfg.get("name_template"), str):
        cfg["name"] = _render_template(
            cfg.pop("name_template"), team_id, bridge_base)
    if isinstance(cfg.get("vlan_template"), str):
        cfg["vlan"] = _js_parse_int(_render_template(
            cfg.pop("vlan_template"), team_id, bridge_base))
    if (isinstance(id_offset, dict)
            and isinstance(id_offset.get("vmid"), (int, float))
            and not isinstance(id_offset.get("vmid"), bool)
            and "vm_id" in cfg):
        base_vmid = _js_number(cfg["vm_id"])
        if base_vmid is not None:
            # Multiply by the raw offset, not int(): TS does plain number
            # arithmetic, so a fractional vmid offset must stay fractional.
            # Truncating gave every team the same id — duplicate VMIDs.
            cfg["vm_id"] = _num_to_str_value(
                base_vmid + id_offset["vmid"] * team_id)
    out["config"] = cfg
    for tkey, okey in _NODE_TEMPLATES:
        if isinstance(out.get(tkey), str):
            out[okey] = _render_template(out.pop(tkey), team_id, bridge_base)
    if isinstance(out.get("networks"), list):
        for nw in out["networks"]:
            if isinstance(nw, dict) and isinstance(nw.get("ip_template"), str):
                nw["ip"] = _render_template(
                    nw.pop("ip_template"), team_id, bridge_base)
    # Rewrite play-level notify targets inside attachments.
    atts = out.get("attachments")
    for att in atts if isinstance(atts, list) else []:
        if not isinstance(att, dict):
            continue
        if isinstance(att.get("notify"), list):
            att["notify"] = [f"{_js_str(n)}__team_{team_id}"
                             for n in att["notify"]]
        elif isinstance(att.get("notify"), str):
            att["notify"] = f"{att['notify']}__team_{team_id}"
        if att.get("ansible_primitive") == "handler":
            raw_ns = att.get("handler_namespace")
            base_ns = raw_ns if isinstance(raw_ns, str) else ""
            ns = f"{base_ns}__team_{team_id}" if base_ns else f"team_{team_id}"
            att["handler_namespace"] = ns
            namespace_sink.append(ns)
    return out


def _walk_and_expand(nodes: list[dict], team_count: int,
                     namespace_sink: list[str],
                     bridge_base: int) -> list[dict]:
    result: list[dict] = []
    for n in nodes:
        raw_rep = n.get("replication")
        rep = raw_rep if isinstance(raw_rep, dict) else {}
        # `scope:` with no value is a present key holding None — .get(k, default)
        # would return None and fall through to the per_team branch, expanding
        # a node the author marked shared. TS coalesces with ??, which is
        # nullish and NOT truthiness: false / 0 / "" keep their value and fall
        # through to per_team. `or` would swallow them into "shared".
        raw_scope = rep.get("scope")
        scope = "shared" if raw_scope is None else raw_scope
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
        raw_offset = rep.get("id_offset")
        id_offset = raw_offset if isinstance(raw_offset, dict) else {}
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
    if not isinstance(team_count, int) or isinstance(team_count, bool):
        raise ValueError(f"invalid team_count: {team_count!r}")
    if team_count < 1:
        raise ValueError(f"invalid team_count: {team_count}")
    out = deepcopy(doc)
    namespace_sink: list[str] = []
    # Mirrors TS `typeof x === "number"`: any non-bool number wins, so a
    # schema-valid float (200.0 IS an integer in JSON Schema) renders the
    # same on both sides instead of silently falling back to the default.
    raw_base = doc.get("bridge_base")
    bridge_base = raw_base if isinstance(raw_base, (int, float)) and not isinstance(
        raw_base, bool) else DEFAULT_BRIDGE_BASE
    raw_nodes = doc.get("nodes")
    out["nodes"] = _walk_and_expand(
        raw_nodes if isinstance(raw_nodes, list) else [],
        team_count, namespace_sink, bridge_base)
    return {
        "plays_per_team": team_count,
        "handler_namespaces": namespace_sink,
        "document": out,
    }
