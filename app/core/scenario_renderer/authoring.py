"""Guided bundle authoring -- the grammar, enforced at creation time.

A bundle is a verb: a reusable action that takes its target as a call-site
parameter and owns no identity. Its name is therefore an action name, and the
grammar is ``<tier>/<subject>.<verb>[.<object>]`` -- the verb ALWAYS the second
dot-segment, drawn from closed vocabularies. Closed is the point: a new subject
or verb is a deliberate decision taken here, not an ad-hoc synonym invented at
the moment of writing ``main.yml``.

This module is what a "create a new bundle" wizard calls: it validates a proposed
name and emits a conformant, runnable skeleton. It is pure -- it returns file
contents, it never writes them. The error messages are a UX surface: each names
both what is wrong and what is legal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.scenario_renderer._common import BUNDLES_ROOT
from app.core.scenario_renderer.registry import BundleKind

GRAMMAR = "<tier>/<subject>.<verb>[.<object>]"

#: Tiers the grammar governs. ``ctf`` is deliberately absent -- see ``EXEMPT_TIERS``.
ACTION_TIERS = ("admin", "core")

#: Identity-addressed tiers: a CTF challenge is "which CVE", not "which action",
#: so subject.verb.object says nothing about it and must not be forced onto it.
EXEMPT_TIERS = ("ctf",)

SUBJECTS = ("software", "system", "network", "credentials", "repo", "template", "vm")

VERBS = ("install", "build", "create", "configure", "baseline", "clone", "bootstrap")

_SEGMENT = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

#: The synonyms contributors actually reach for, mapped to the verb they meant.
#: Naming the right verb is worth more than listing the vocabulary at them.
_VERB_SYNONYMS = {
    "setup": "install",
    "deploy": "install",
    "provision": "install",
    "add": "install",
    "make": "build",
    "compile": "build",
    "generate": "create",
    "new": "create",
    "init": "bootstrap",
    "initialize": "bootstrap",
    "harden": "baseline",
    "prepare": "baseline",
    "copy": "clone",
    "pull": "clone",
    "checkout": "clone",
    "set": "configure",
    "tune": "configure",
}

#: What each kind must be told at its call-site in order to know what to act on.
_REQUIRED_VARS: dict[BundleKind, tuple[str, ...]] = {
    BundleKind.VM: ("global_vm_ssh_name", "global_vm_ci_ip"),
    BundleKind.GROUP: ("target_group",),
    BundleKind.INFRA: (),
}

_KIND_BLURB = {
    BundleKind.VM: 'attaches to ONE VM (hosts: "{{ global_vm_ssh_name }}")',
    BundleKind.GROUP: 'attaches to a tier\'s group (hosts: "{{ target_group }}")',
    BundleKind.INFRA: "runs on hosts: proxmox, acts on the scenario (no host parameter)",
}

_VAR_PURPOSES = {
    "global_vm_ssh_name": "inventory hostname of the target VM",
    "global_vm_ci_ip": "IP of the target VM",
    "target_group": "inventory group this bundle acts on",
}

_EXAMPLE_SSH_NAME = "r42.example-vm"
_EXAMPLE_IP = "192.168.142.10"
_EXAMPLE_GROUP = "r42_admin_group"


@dataclass(frozen=True)
class BundleName:
    """A parsed action-bundle name."""

    tier: str
    subject: str
    verb: str
    object: str | None = None

    def __str__(self) -> str:
        return f"{self.tier}/{self.action}"

    @property
    def action(self) -> str:
        """The name without its tier -- what the play is named after."""
        return ".".join(part for part in (self.subject, self.verb, self.object) if part)


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(repr(value) for value in values)


def _kebabed(segment: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", segment.lower()).strip("-")


def _validate_segment(segment: str, name: str) -> None:
    if _SEGMENT.match(segment):
        return
    suggestion = _kebabed(segment)
    hint = f" Did you mean {suggestion!r}?" if suggestion and suggestion != segment else ""
    raise ValueError(
        f"invalid segment {segment!r} in {name!r}: segments are lowercase kebab-case "
        f"-- [a-z0-9] joined by '-', e.g. 'deployer-api-backend'. No uppercase, no "
        f"underscores, no spaces, no empty segments.{hint}"
    )


def _reject_misplaced_verb(name: str, tier: str, segments: list[str]) -> None:
    """Catch a legal verb sitting anywhere but second.

    ``install.software.docker`` and ``software.docker.install`` both read fine and
    decompose into plausible segments -- only their *position* is wrong, so the
    vocabulary check alone would blame the innocent segment now standing second.
    """
    misplaced = [segment for segment in segments if segment in VERBS]
    if not misplaced:
        return

    verb = misplaced[0]
    position = "subject" if segments[0] == verb else "object"
    others = [segment for segment in segments if segment != verb]
    reordered = ".".join([others[0], verb, *others[1:]])
    raise ValueError(
        f"invalid bundle name {name!r}: {verb!r} is a verb, but it sits in the "
        f"{position} position -- in {GRAMMAR} the verb is always the second segment. "
        f"Did you mean '{tier}/{reordered}'?"
    )


def parse_bundle_name(name: str) -> BundleName:
    """Validate and decompose a proposed bundle name against the grammar.

    Raises ``ValueError`` naming both what is wrong and what is legal.
    """
    parts = name.split("/")

    if parts[0] in EXEMPT_TIERS:
        raise ValueError(
            f"{parts[0]!r} is exempt from the bundle grammar: a challenge is "
            f"identity-addressed by its taxonomy path (which CVE -- e.g. "
            f"'ctf/cve/web/tomcat/CVE-2025-24813'), not action-addressed, so "
            f"{GRAMMAR} does not apply to {name!r}. Do not rename it into the "
            f"grammar and do not scaffold it with this tool."
        )

    if len(parts) != 2 or not parts[1]:
        raise ValueError(
            f"invalid bundle name {name!r}: expected exactly one '/', separating tier "
            f"from name -- {GRAMMAR}, e.g. 'admin/software.install.gitea'. Legal tiers "
            f"are {_quoted(ACTION_TIERS)}."
        )

    tier, rest = parts
    _validate_segment(tier, name)
    if tier not in ACTION_TIERS:
        raise ValueError(
            f"unknown tier {tier!r} in {name!r}: legal tiers are {_quoted(ACTION_TIERS)}. "
            f"({_quoted(EXEMPT_TIERS)} exists but is exempt from this grammar.)"
        )

    segments = rest.split(".")
    if not 2 <= len(segments) <= 3:
        raise ValueError(
            f"invalid bundle name {name!r}: expected 2 or 3 dot-segments -- {GRAMMAR}, "
            f"e.g. 'core/vm.bootstrap' (generic) or 'admin/software.install.gitea' "
            f"(specialised); got {len(segments)} ({_quoted(tuple(segments))}). Words "
            f"inside one segment are joined with '-', not '.'."
        )

    for segment in segments:
        _validate_segment(segment, name)

    subject, verb, *tail = segments

    if verb not in VERBS:
        _reject_misplaced_verb(name, tier, segments)
        hint = _VERB_SYNONYMS.get(verb)
        raise ValueError(
            f"unknown verb {verb!r} in {name!r}: "
            + (f"did you mean {hint!r}? " if hint else "")
            + f"the verb is always the second segment of {GRAMMAR} and the vocabulary "
            f"is closed -- legal verbs are {_quoted(VERBS)}. Synonyms are not accepted; "
            f"adding a verb is a deliberate decision, made in VERBS."
        )

    if subject not in SUBJECTS:
        raise ValueError(
            f"unknown subject {subject!r} in {name!r}: the subject is the first segment "
            f"of {GRAMMAR} and the vocabulary is closed -- legal subjects are "
            f"{_quoted(SUBJECTS)}. A concrete thing like 'docker' or 'gitea' is an "
            f"OBJECT, not a subject: 'admin/software.install.gitea'."
        )

    return BundleName(tier=tier, subject=subject, verb=verb, object=tail[0] if tail else None)


def _playbook_path(name: BundleName) -> str:
    """Where the call-site imports this bundle from -- env-anchored, so a rendered
    scenario finds it from a project repo, not only from range42-playbooks."""
    return f"{BUNDLES_ROOT}/{name}/main.yml"


def _example_vars(kind: BundleKind) -> dict[str, str]:
    return {
        BundleKind.VM: {
            "global_vm_ssh_name": _EXAMPLE_SSH_NAME,
            "global_vm_ci_ip": _EXAMPLE_IP,
        },
        BundleKind.GROUP: {"target_group": _EXAMPLE_GROUP},
        BundleKind.INFRA: {},
    }[kind]


def _hosts(kind: BundleKind) -> str:
    return {
        BundleKind.VM: '"{{ global_vm_ssh_name }}"',
        BundleKind.GROUP: '"{{ target_group }}"',
        BundleKind.INFRA: "proxmox",
    }[kind]


def _gate(name: BundleName, kind: BundleKind) -> str | None:
    """The flag a scenario gates this bundle on, when gating it is idiomatic.

    Only a *specialised* VM bundle gets a gate: that is the stage_01 pattern
    (``INSTALL_GITEA``). The generic form (no object) and the group/infra bundles
    are the baseline of a scenario -- they run unconditionally.
    """
    if kind is not BundleKind.VM or not name.object:
        return None
    flag = f"INSTALL_{name.object.upper().replace('-', '_')}"
    return f'{flag} | default("NO") | upper == "YES"'


def _call_site(name: BundleName, kind: BundleKind, indent: str = "") -> str:
    lines = [f'- import_playbook: "{_playbook_path(name)}"']
    gate = _gate(name, kind)
    if gate:
        lines.append(f"  when: {gate}")
    example_vars = _example_vars(kind)
    if example_vars:
        lines.append("  vars:")
        width = max(len(key) for key in example_vars) + 1
        lines += [f'    {key + ":":<{width}} "{value}"' for key, value in example_vars.items()]
    return "\n".join(indent + line for line in lines)


def _main_yml(name: BundleName, kind: BundleKind, description: str) -> str:
    banner = "# " + "=" * 74
    header = [
        banner,
        f"# bundles/{name}/main.yml",
        banner,
        f"# {description}",
        "#",
        f"# KIND : {kind.value} -- {_KIND_BLURB[kind]}",
        "#",
    ]
    required = _REQUIRED_VARS[kind]
    if required:
        example = _example_vars(kind)
        width = max(len(var) for var in required)
        header.append("# REQUIRED vars (passed by the calling scenario via import_playbook `vars:`) :")
        header += [
            f"#   - {var:<{width}} : {_VAR_PURPOSES[var]} (e.g. \"{example[var]}\")"
            for var in required
        ]
    else:
        header.append("# REQUIRED vars : none -- this bundle takes no host parameter.")
    header += ["#", "# Example call-site :", "#"]
    header += ["#   " + line if line else "#" for line in _call_site(name, kind).splitlines()]
    header += ["#", banner, ""]

    play = [
        f"- name: {name.action} - TODO name this play after what it does",
        f"  hosts: {_hosts(kind)}",
        # the proxmox connection is already privileged; a VM lands us as the
        # unprivileged cloud-init operator, so there become is not optional
        *([] if kind is BundleKind.INFRA else ["  become: true"]),
        f"  gather_facts: {'true' if kind is BundleKind.VM else 'false'}",
        "  tasks:",
        "    - name: TODO replace with this bundle's roles or tasks",
        "      ansible.builtin.debug:",
        f'        msg: "{name.action} - not implemented yet"',
        "",
    ]
    return "\n".join(header + play)


def _readme_md(name: BundleName, kind: BundleKind, description: str) -> str:
    required = _REQUIRED_VARS[kind]
    lines = [
        f"# bundles/{name}",
        "",
        description,
        "",
        f"- **kind** : `{kind.value}` -- {_KIND_BLURB[kind]}",
        "",
        "## Required vars",
        "",
    ]
    if required:
        example = _example_vars(kind)
        lines += ["| var | example | purpose |", "|-----|---------|---------|"]
        lines += [f"| `{var}` | `{example[var]}` | {_VAR_PURPOSES[var]} |" for var in required]
    else:
        lines.append("None -- this bundle runs on `hosts: proxmox` and takes no host parameter.")
    lines += [
        "",
        "## Call-site",
        "",
        "```yaml",
        _call_site(name, kind),
        "```",
        "",
        "## TODO",
        "",
        "- [ ] replace the placeholder task in `main.yml` with the real roles / tasks",
        "- [ ] register this bundle in the renderer's registry "
        "(`app/core/scenario_renderer/registry.py`) so scenarios can address it",
        "",
    ]
    return "\n".join(lines)


def scaffold_bundle(name: str, kind: BundleKind, description: str = "") -> dict[str, str]:
    """Render a new, conformant bundle: relative file path -> file content.

    Pure: the caller decides where (and whether) to write. The name is parsed
    first, so an off-grammar bundle is never scaffolded into existence.
    """
    parsed = parse_bundle_name(name)

    if kind is BundleKind.XTIER:
        raise ValueError(
            f"cannot scaffold {name!r} as kind 'xtier': an xtier bundle acts on a group "
            f"assembled across tiers (e.g. wazuh_clients_group), which the SCENARIO "
            f"builds -- the bundle cannot know its shape, so there is no generic "
            f"skeleton for it. Scaffold it as "
            f"{_quoted(('vm', 'group', 'infra'))} and adapt, or copy the closest "
            f"existing xtier bundle."
        )

    description = description or f"TODO: one line -- what {parsed.action} does, and to what."
    return {
        "main.yml": _main_yml(parsed, kind, description),
        "README.md": _readme_md(parsed, kind, description),
    }
