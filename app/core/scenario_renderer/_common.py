"""Shared primitives used by several renderers.

These were duplicated across modules built in parallel. Centralising them removes
the real hazard: the env-anchored path and the INSTALL-gate expression appearing
in more than one place, where a repo rename or a guard-syntax change could update
one copy and silently desync the rendered tree.
"""

from __future__ import annotations

# Bundle imports are anchored on this env var (never a relative or absolute path),
# which is what lets a rendered scenario live in a project repo and still resolve
# its bundles. A repo rename changes only this line.
PLAYBOOKS_ROOT = "{{ lookup('env', 'RANGE42_GITDIR__ROOT_DIR') }}/range42-playbooks"
BUNDLES_ROOT = f"{PLAYBOOKS_ROOT}/bundles"
SCENARIOS_ROOT = f"{PLAYBOOKS_ROOT}/scenarios"

# add_host plays that build runtime groups run on the one host every scenario's
# inventory always defines.
ADD_HOST_RUNNER = "proxmox"


def gate_expr(flag: str, default: str) -> str:
    """One ``INSTALL_<FLAG>`` truthiness test, exactly as an Ansible ``when`` reads it."""
    return f'INSTALL_{flag} | default("{default}") | upper == "YES"'
