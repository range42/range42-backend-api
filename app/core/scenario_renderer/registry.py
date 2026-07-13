"""Bundle registry -- the descriptor: how a bundle is addressed, and what it attaches to.

Addressing. Action bundles use the grammar ``<tier>/<subject>.<verb>[.<object>]``,
verb always the second segment. ``admin/`` already conforms on disk. ``core/`` has
not been migrated, so each grammar name is aliased to its current nested/kebab path;
when core/ is renamed, delete the alias -- nothing else moves. ``ctf/`` is exempt
from the grammar by design: a challenge is identity-addressed (which CVE), not
action-addressed, so it keeps its taxonomy path.

Kind. A bundle's kind is its *call-site contract* -- the variable it must be told in
order to know what to act on. It decides where a bundle may legally be attached, and
it is the descriptor a UI palette needs to know what can be dropped on what.
"""

from __future__ import annotations

from enum import Enum

_BUNDLES_ROOT = "{{ lookup('env', 'RANGE42_GITDIR__ROOT_DIR') }}/range42-playbooks/bundles"


class BundleKind(Enum):
    """What a bundle needs at its call-site, and therefore what it attaches to."""

    VM = "vm"  # needs global_vm_ssh_name -> one VM
    GROUP = "group"  # needs target_group -> a tier's baseline
    XTIER = "xtier"  # needs a group assembled across tiers (e.g. wazuh_clients_group)
    INFRA = "infra"  # needs no host parameter -> runs on proxmox, acts on the scenario


# grammar name -> (physical path, kind). Path is omitted where it equals the name.
_CORE = {
    "core/vm.bootstrap": ("core/proxmox/configure/vm-bootstrap", BundleKind.VM),
    "core/system.baseline.default": ("core/system-baseline-default", BundleKind.GROUP),
    "core/system.baseline.docker-host": ("core/system-baseline-docker-host", BundleKind.GROUP),
    "core/system.baseline.with-utils": ("core/system-baseline-with-utils", BundleKind.GROUP),
    "core/network.baseline.ssh": ("core/network-baseline-ssh", BundleKind.GROUP),
    "core/network.baseline.ssh-http": ("core/network-baseline-ssh-http", BundleKind.GROUP),
    "core/network.baseline.kong": ("core/network-baseline-kong", BundleKind.GROUP),
    "core/network.baseline.deployer-ui": ("core/network-baseline-deployer-ui", BundleKind.GROUP),
    "core/network.baseline.deployer-backend-api": (
        "core/network-baseline-deployer-backend-api",
        BundleKind.GROUP,
    ),
    "core/software.install.tailscale": ("core/tailscale-on-group", BundleKind.GROUP),
    "core/repo.clone": ("core/gitclone", BundleKind.GROUP),
    "core/repo.clone.kunai": ("core/gitclone-kunai-repository", BundleKind.GROUP),
    "core/template.build.ubuntu-noble": (
        "core/proxmox/configure/templates/ubuntu_noble",
        BundleKind.INFRA,
    ),
    "core/template.build.alpine": ("core/proxmox/configure/templates/alpine", BundleKind.INFRA),
    "core/template.build.debian": ("core/proxmox/configure/templates/debian", BundleKind.INFRA),
}

_ADMIN = {
    f"admin/software.install.{obj}": (None, kind)
    for obj, kind in (
        ("wazuh", BundleKind.VM),
        ("gitea", BundleKind.VM),
        ("kong", BundleKind.VM),
        ("mattermost", BundleKind.VM),
        ("misp-standalone", BundleKind.VM),
        ("nextcloud", BundleKind.VM),
        ("rocketchat", BundleKind.VM),
        ("deployer-ui", BundleKind.VM),
        ("deployer-api-backend", BundleKind.VM),
        # the agent installs onto a client group assembled across tiers, not onto a
        # single VM -- this is why a scenario using it ends on a cross-tier finalize.
        ("wazuh-agent", BundleKind.XTIER),
    )
}

_CTF = {
    f"ctf/{path}": (None, BundleKind.VM)
    for path in (
        "cve/crypto/openssl/CVE-2014-0160",
        "cve/crypto/openssl/CVE-2022-0778",
        "cve/network/erlang-ssh/CVE-2025-32433",
        "cve/network/openssh/CVE-2018-15473",
        "cve/network/openssh/CVE-2024-6387",
        "cve/system/sudo/CVE-2023-22809",
        "cve/system/sudo/CVE-2025-32462",
        "cve/system/sudo/CVE-2025-32463",
        "cve/web/apache/CVE-2021-42013",
        "cve/web/pdfjs/CVE-2024-4367",
        "cve/web/php/CVE-2019-11043",
        "cve/web/tomcat/CVE-2025-24813",
        "cve/web/uwsg_php/CVE-2018-7490",
        "cve/web/vite/CVE-2025-30208",
        "misconfiguration/network/vsftpd/ftp_anon_server",
        "misconfiguration/system/lpe-01",
    )
}

_BUNDLES: dict[str, tuple[str | None, BundleKind]] = {**_CORE, **_ADMIN, **_CTF}


def _lookup(name: str) -> tuple[str | None, BundleKind]:
    try:
        return _BUNDLES[name]
    except KeyError:
        raise KeyError(f"unknown bundle: {name}") from None


def resolve_bundle_playbook(name: str) -> str:
    """Resolve a bundle name to the env-anchored path of its ``main.yml``.

    Env-anchored (not relative) is what makes a rendered scenario portable: it can
    live in a project repo instead of the playbooks repo and still find its bundles.
    """
    path, _ = _lookup(name)
    return f"{_BUNDLES_ROOT}/{path or name}/main.yml"


def bundle_kind(name: str) -> BundleKind:
    """The bundle's call-site contract -- what it must be told to act on."""
    _, kind = _lookup(name)
    return kind


def is_vm_level(name: str) -> bool:
    """True when the bundle may be attached to a single VM.

    A GROUP bundle attached to a VM would be handed ``global_vm_ssh_name`` while it
    reads ``target_group``, and would silently target nothing at deploy time.
    """
    return bundle_kind(name) is BundleKind.VM


def known_bundles() -> frozenset[str]:
    """Every bundle name the renderer can address."""
    return frozenset(_BUNDLES)
