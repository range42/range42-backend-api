"""Native scenario capabilities, independent of the canvas-generated contract."""
from __future__ import annotations

import json
import math
from pathlib import Path
import re

import yaml

from app.core.catalog_index import _scenario
from app.core.errors import Range42Error

# Names are API actions. Values are native wrapper suffixes and YAML fallbacks.
NATIVE_ACTIONS = {
    "full": ("setup.sh", ("main.yml", "main.yaml")),
    "configure": ("configure.sh", ("configure.yml", "configure.yaml")),
    "deploy_vms": ("setup_vms_only.sh", ("main_vms_only.yml",)),
    "teardown": ("delete_all.sh", ("teardown.yml",)),
    "delete_vms": ("delete_vms_only.sh", ()),
    "reset": ("reset.setup.sh", ()),
    "deploy_networks": ("setup_networks.sh", ()),
    "delete_networks": ("delete_networks.sh", ()),
}
DESTRUCTIVE_ACTIONS = {"teardown", "delete_vms", "delete_networks", "reset"}


def invalid(message: str, code="NATIVE_SCENARIO_INVALID") -> Range42Error:
    return Range42Error(code=code, error="native_scenario_invalid", status=409, message=message)


def inside(root: Path, relative: str, *, file: bool = False) -> Path:
    if (not isinstance(relative, str) or not relative or relative.startswith("/")
            or "\\" in relative or any(ord(c) < 32 for c in relative)
            or any(p in {"", ".", "..", ".git"} for p in relative.split("/"))):
        raise invalid("Native paths must stay inside the saved repository")
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()) or (file and not path.is_file()):
        raise invalid(f"Native scenario file is missing or outside the repository: {relative}")
    return path


def inspect_native_scenario(root: Path, path: str) -> dict:
    root = root.resolve()
    base = inside(root, path)
    entry = _scenario(base, root)
    if entry is None:
        raise invalid("Choose a native scenario containing main.yml and manifest/scenario_vms.json")
    manifest = json.loads((base / "manifest/scenario_vms.json").read_text())
    options = {}
    runtime = base / "manifest/scenario_runtime.json"
    if runtime.exists() or runtime.is_symlink():
        runtime = inside(base, "manifest/scenario_runtime.json", file=True)
        try:
            if runtime.stat().st_size > 65536:
                raise ValueError()
            options = json.loads(runtime.read_text())
            if (not isinstance(options, dict) or options.get("version") != 1
                    or set(options) - {"version", "actions", "bundle_path"}
                    or not isinstance(options.get("actions", {}), dict)
                    or set(options.get("actions", {})) - NATIVE_ACTIONS.keys()):
                raise ValueError()
        except (OSError, ValueError, TypeError):
            raise invalid("Invalid manifest/scenario_runtime.json") from None
    actions = {}
    for action, (suffix, fallbacks) in NATIVE_ACTIONS.items():
        explicit = options.get("actions", {}).get(action)
        if explicit is not None:
            candidate = inside(base, explicit, file=True)
            if candidate.suffix not in {".sh", ".yml", ".yaml"}:
                raise invalid("Native entrypoints must be shell scripts or Ansible playbooks")
            actions[action] = explicit
            continue
        exact = base / f"{base.name}.{suffix}"
        candidates = [exact] if exact.exists() or exact.is_symlink() else sorted(base.glob(f"*.{suffix}"))
        if action == "full":
            candidates = [p for p in candidates if not p.name.endswith(".reset.setup.sh")]
        if len(candidates) > 1:
            raise invalid(f"Native action {action} is ambiguous; declare its entrypoint in manifest/scenario_runtime.json")
        candidate = candidates[0] if candidates else next((base / name for name in fallbacks if (base / name).is_file()), None)
        if candidate:
            inside(base, candidate.relative_to(base).as_posix(), file=True)
            actions[action] = candidate.relative_to(base).as_posix()
    bundle_path = options.get("bundle_path", "bundles" if (root / "bundles").is_dir() else None)
    if bundle_path is not None and not inside(root, bundle_path).is_dir():
        raise invalid("Native bundle_path must name a directory in the saved repository")
    inventory = "templates/ansible-inventory.j2"
    inside(base, inventory, file=True)
    return {"version": 1, "path": path, "name": base.name,
            "actions": actions, "features": entry["document"]["features"],
            "bundle_path": bundle_path, "inventory_template": inventory,
            "declared_vmids": [vm["vm_id"] for vm in manifest["vms"]],
            "vmid_parameters": _vmid_parameters(base),
            "impact": "native_workflow"}


def _vmid_parameters(base: Path) -> dict[str, list[int]]:
    """Include literal VM targets outside the manifest, such as SDN diagnostics.

    This is deliberately not a complete impact analysis of arbitrary Ansible.
    Dynamic targets remain covered by the native-workflow preflight warning.
    """
    found: dict[str, set[int]] = {}
    def walk(value, seen):
        if not isinstance(value, (dict, list)) or id(value) in seen:
            return
        seen.add(id(value))
        if isinstance(value, dict):
            for name in ("set_fact", "ansible.builtin.set_fact"):
                values = value.get(name)
                if isinstance(values, dict):
                    for key, item in values.items():
                        if (isinstance(key, str) and re.search(r"(?:^|_)(?:vm_id|vmid)$", key, re.I)
                                and "template" not in key.lower()
                                and type(item) is int and item > 0):
                            found.setdefault(key, set()).add(item)
            for item in value.values():
                walk(item, seen)
        else:
            for item in value:
                walk(item, seen)
    for path in base.rglob("*"):
        if (path.suffix not in {".yml", ".yaml"} or not path.is_file()
                or not path.resolve().is_relative_to(base.resolve()) or path.stat().st_size > 2 * 1024 * 1024):
            continue
        try:
            walk(yaml.safe_load(path.read_text()), set())
        except (ValueError, OSError, yaml.YAMLError, RecursionError):
            continue  # The native playbook remains authoritative for custom syntax.
    return {key: sorted(value) for key, value in sorted(found.items())}


def native_vmids(descriptor: dict, parameters: dict) -> list[int]:
    requested = set()
    for key, defaults in descriptor.get("vmid_parameters", {}).items():
        values = [parameters[key]] if key in parameters else defaults
        for value in values:
            if type(value) is not int or value <= 0:
                raise invalid(f"Native VM target {key} must be a positive integer")
            requested.add(value)
    for key, value in parameters.items():
        if re.search(r"(?:^|_)(?:vm_id|vmid)$", key, re.I):
            if type(value) is not int or value <= 0:
                raise invalid(f"Native VM target {key} must be a positive integer")
            requested.add(value)
    declared = descriptor["declared_vmids"]
    return declared + sorted(requested - set(declared))


def native_variables(descriptor: dict, features: dict, parameters: dict) -> dict:
    declared = {feature["id"]: feature["default"] for feature in descriptor["features"]}
    if (not isinstance(features, dict) or set(features) - declared.keys()
            or any(type(value) is not bool for value in features.values())):
        raise invalid("Feature selections must use declared names and boolean values")
    if not isinstance(parameters, dict) or len(json.dumps(parameters)) > 16384 or len(parameters) > 64:
        raise invalid("Native parameters must be a bounded JSON object")
    for key, value in parameters.items():
        if (not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", key)
                or key.lower().startswith(("ansible_", "range42_", "r42_", "proxmox_", "deployer_", "infrastructure_", "install_"))
                or re.search(r"(?:^|_)(?:password|passphrase|secret|token|private_key|api_key)(?:_|$)", key, re.I)
                or type(value) not in {str, bool, int, float}
                or (isinstance(value, float) and not math.isfinite(value))
                or any(marker in str(value) for marker in ("{{", "{%", "{#"))):
            raise invalid(f"Native parameter {key} is invalid or managed by the selected context")
    return {**parameters, **{f"INSTALL_{name}": "YES" if features.get(name, default) else "NO"
                             for name, default in declared.items()}}
