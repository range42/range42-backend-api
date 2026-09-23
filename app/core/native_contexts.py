"""Read existing deployer-cli contexts. Discovery never sources shell files."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit

import yaml

from app.core.native_scenarios import invalid


@dataclass(frozen=True)
class NativeContext:
    id: str
    label: str
    workspace: Path
    context_script: Path
    codename: str
    scenario: str
    address: str
    inventory_variables: dict = field(default_factory=dict)

    def issues(self) -> list[str]:
        required = {
            self.workspace / "sourced_range42.sh": "Context environment is missing; initialize this workspace with range42-context init.",
            self.workspace / "inventory/inventory_default.yml": "Context inventory is missing; initialize this workspace with range42-context init.",
            self.workspace / "secrets/default_vault.yml": "Context vault is missing.",
            self.workspace / "secrets/vault_pass.txt": "Context vault password file is missing.",
            self.context_script: "Install range42-context on the deployer-cli and set RANGE42_CONTEXT_SCRIPT.",
        }
        issues = [message for path, message in required.items() if not path.is_file()]
        if self.context_script.is_file():
            script = self.context_script.read_text()
            if re.search(r'if \[\[ -f "\$scenario_target/main.yml" \]\]; then\s*_r42_run_concrete main.yml', script):
                issues.append("This range42-context adapter treats every main.yml as generated. Install the native SDN context runtime.")
        if not self.codename or not self.scenario:
            issues.append("Context environment does not declare its codename and scenario.")
        elif self.workspace.name != f"{self.codename}-{self.scenario}":
            issues.append("Context workspace directory must match its codename-scenario name for range42-context use.")
        if not self.address:
            issues.append("Context inventory does not identify one Proxmox target.")
        if any(not shutil.which(name) for name in ("zsh", "ansible-playbook", "ansible-vault", "ssh", "ssh-agent", "ssh-add", "jq", "yq")):
            issues.append("Install zsh, Ansible, OpenSSH, jq and yq on the deployer-cli running this API.")
        return issues


def _literal_export(text: str, name: str) -> str:
    match = re.search(r"^export " + re.escape(name) + r"=['\"]([^'\"$`\n]+)['\"]\s*$", text, re.M)
    return match[1] if match else ""


def _address(inventory: Path) -> str:
    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "proxmox" and isinstance(item, dict):
                    for data in (item.get("hosts") or {}).values():
                        if isinstance(data, dict) and isinstance(data.get("ansible_host"), str):
                            yield data["ansible_host"]
                yield from walk(item)
    try:
        if inventory.stat().st_size > 2 * 1024 * 1024:
            return ""
        addresses = {urlsplit(value if "://" in value else "https://" + value).hostname
                     for value in walk(yaml.safe_load(inventory.read_text()))}
        return next(iter(addresses)) or "" if len(addresses) == 1 else ""
    except (OSError, ValueError, yaml.YAMLError):
        return ""


def _contexts() -> list[NativeContext]:
    registry = os.getenv("RANGE42_NATIVE_CONTEXTS_FILE")
    if registry:
        try:
            path = Path(registry)
            if path.stat().st_size > 1024 * 1024:
                raise ValueError()
            data = json.loads(path.read_text())
            rows = data["contexts"]
            if data.get("version") != 1 or not isinstance(rows, list) or len(rows) > 256:
                raise ValueError()
        except (OSError, ValueError, KeyError, TypeError):
            raise invalid("The operator's native context registry is invalid", "NATIVE_CONTEXT_UNAVAILABLE") from None
    else:
        root = Path(os.getenv("RANGE42_CONTEXT_ROOT", str(Path.home() / "range42.config")))
        rows = [{"id": path.name, "workspace": str(path)} for path in sorted(root.iterdir())
                if path.is_dir() and (path / "sourced_range42.sh").is_file()] if root.is_dir() else []
    result = []
    seen = set()
    for row in rows:
        try:
            identity = row["id"]
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", identity) or identity in seen:
                raise ValueError()
            seen.add(identity)
            workspace = Path(row["workspace"]).resolve()
            env_path = workspace / "sourced_range42.sh"
            env = env_path.read_text() if env_path.is_file() and env_path.stat().st_size <= 65536 else ""
            git_root = _literal_export(env, "RANGE42_GITDIR__ROOT_DIR")
            fallback_script = str(Path(git_root) / "range42/roles/deployer.bootstrap/files/range42-context.sh") if git_root else "/nonexistent/range42-context.sh"
            script = Path(row.get("context_script") or os.getenv("RANGE42_CONTEXT_SCRIPT") or fallback_script).resolve()
            variables = row.get("inventory_variables", {})
            if not isinstance(variables, dict):
                raise ValueError()
            result.append(NativeContext(identity, row.get("label") or identity, workspace, script,
                _literal_export(env, "RANGE42_INFRASTRUCTURE_CODENAME"),
                _literal_export(env, "RANGE42_INFRASTRUCTURE_LAB"),
                _address(workspace / "inventory/inventory_default.yml"), variables))
        except (ValueError, TypeError, KeyError, OSError):
            raise invalid("The operator's native context registry contains an invalid entry", "NATIVE_CONTEXT_UNAVAILABLE") from None
    return result


def available_contexts(hosts: list) -> list[dict]:
    rows = []
    for context in _contexts():
        matches = [host for host in hosts if urlsplit(host.api_url).hostname == context.address]
        issues = context.issues()
        if len(matches) != 1:
            issues.append("Register exactly one matching Proxmox target, or resolve duplicate target registrations in Settings.")
        rows.append({"id": context.id, "label": context.label, "codename": context.codename,
                     "scenario": context.scenario, "target_host_id": matches[0].id if len(matches) == 1 else None,
                     "ready": not issues, "issues": issues})
    return rows


def resolve_context(identity: str, host) -> NativeContext:
    context = next((value for value in _contexts() if value.id == identity), None)
    if context is None:
        raise invalid("Choose an existing Range42 context from the environment list", "NATIVE_CONTEXT_UNAVAILABLE")
    if host is None or urlsplit(host.api_url).hostname != context.address:
        raise invalid("The selected context and registered target must address the same Proxmox host", "NATIVE_CONTEXT_TARGET_MISMATCH")
    if issues := context.issues():
        raise invalid(" ".join(issues), "NATIVE_CONTEXT_UNAVAILABLE")
    return context
