"""Discover source-owned catalog entries without a hardcoded bundle registry."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import yaml

_log = logging.getLogger(__name__)
_METADATA_LIMIT = 2 * 1024 * 1024


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def _strings(value) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _safe_file(path: Path, root: Path) -> bool:
    try:
        return (path.resolve().is_relative_to(root.resolve()) and path.is_file()
                and ".git" not in path.relative_to(root).parts
                and path.stat().st_size <= _METADATA_LIMIT)
    except (OSError, ValueError):
        return False


def _document(path: Path, root: Path):
    if not _safe_file(path, root):
        return None
    try:
        content = path.read_text()
        return json.loads(content) if path.suffix == ".json" else yaml.safe_load(content)
    except (OSError, ValueError, yaml.YAMLError):
        _log.warning("Skipping invalid catalog document %s", path.relative_to(root))
        return None


def _entry(base: Path, root: Path, *, kind: str, name: str | None = None,
           description=None, tags=None, document=None) -> dict:
    return {"path": base.relative_to(root).as_posix() or ".", "kind": kind,
            "name": name if isinstance(name, str) and name else base.name,
            "description": description if isinstance(description, str) else None,
            "tags": _strings(tags), "document": _mapping(document)}


def _bundle_kind(plays: list) -> str:
    kinds = set()
    for play in plays:
        if not isinstance(play, dict):
            return "UNKNOWN"
        hosts = play.get("hosts")
        if hosts is None:
            # Imported playbooks have their own targets; don't guess their scope.
            if "import_playbook" in play or "ansible.builtin.import_playbook" in play:
                return "UNKNOWN"
            continue
        if not isinstance(hosts, str):
            return "UNKNOWN"
        hosts = re.sub(r"\s+", "", hosts).lower()
        if hosts in {"proxmox", "localhost", "127.0.0.1"}:
            kinds.add("INFRA")
        elif hosts in {"{{global_vm_ssh_name}}", "{{target_ansible_host}}"}:
            kinds.add("VM")
        elif hosts in {"{{target_group}}", "{{target_ansible_hosts}}", "{{kunai_target_group}}"}:
            kinds.add("GROUP")
        else:
            return "UNKNOWN"
    if kinds == {"INFRA", "VM"} or kinds == {"INFRA", "GROUP"}:
        return "XTIER"
    return next(iter(kinds)) if len(kinds) == 1 else "UNKNOWN"


def _bundle(base: Path, root: Path) -> dict | None:
    relative = base.relative_to(root)
    if not relative.parts or relative.parts[0] != "bundles" or len(relative.parts) < 3:
        return None
    main = next((base / name for name in ("main.yml", "main.yaml") if _safe_file(base / name, root)), None)
    if main is None:
        return None
    plays = _document(main, root)
    if not isinstance(plays, list):
        return None
    descriptor = _mapping(_document(base / "bundle_parameters.json", root))
    params = descriptor.get("params")
    params = [item for item in params if isinstance(item, dict) and isinstance(item.get("name"), str)] if isinstance(params, list) else []
    name = Path(*relative.parts[1:]).as_posix()
    subject, _, rest = base.name.partition(".")
    verb = rest.split(".", 1)[0] if rest else None
    document = {**descriptor, "bundle": name, "tier": relative.parts[1],
                "entrypoint": main.relative_to(root).as_posix(), "bundle_kind": _bundle_kind(plays),
                "params": params, "subject": subject if rest else None, "verb": verb,
                "grammar_valid": relative.parts[1] == "ctf" or bool(re.fullmatch(
                    r"(?:software|system|systems|network|credentials|repo|template|vm)\."
                    r"(?:install|build|create|configure|baseline|clone|bootstrap)(?:\.[a-z0-9_]+)*", base.name))}
    return _entry(base, root, kind="bundle", name=name, description=descriptor.get("description"),
                  tags=[relative.parts[1], document["bundle_kind"]], document=document)


def detail_at_path(repo_dir: Path, path: str) -> dict | None:
    root = repo_dir.resolve()
    base = (root / path).resolve()
    if not base.is_relative_to(root) or ".git" in base.relative_to(root).parts or not base.is_dir():
        return None
    native = _document(base / "range42.yaml", root)
    if isinstance(native, dict):
        return _entry(base, root, kind=native.get("kind") if isinstance(native.get("kind"), str) else "unknown",
                      name=native.get("name"), description=native.get("description"), tags=native.get("tags"), document=native)
    bundle = _bundle(base, root)
    if bundle is not None:
        return bundle
    metadata = _document(base / "meta.json", root)
    if metadata is not None:
        doc = _mapping(metadata)
        context = _mapping(doc.get("x_range42"))
        return _entry(base, root, kind="container", name=_mapping(context.get("exercise")).get("id"),
                      description=(_mapping(context.get("catalog")).get("description")
                                   or _mapping(context.get("vuln")).get("title")
                                   or _mapping(context.get("misconfig")).get("title")),
                      tags=_mapping(context.get("catalog")).get("tags"), document=doc)
    if "04_gamification_layer" in base.relative_to(root).parts:
        manifest = _document(base / "manifest.json", root)
        if isinstance(manifest, dict):
            return _entry(base, root, kind="gamification", name=manifest.get("name"),
                          description=manifest.get("description"), tags=manifest.get("tags"), document=manifest)
    for name in ("meta/main.yml", "meta/main.yaml"):

        role_metadata = _document(base / name, root)
        if role_metadata is not None:
            galaxy = _mapping(_mapping(role_metadata).get("galaxy_info"))
            return _entry(base, root, kind="ansible_role", description=galaxy.get("description"),
                          tags=galaxy.get("galaxy_tags"), document=role_metadata)
    for name in ("tasks/main.yml", "tasks/main.yaml"):
        if isinstance(_document(base / name, root), list):
            return _entry(base, root, kind="ansible_role", document={"entrypoint": name, "metadata_inferred": True})
    if any(part in {"files", "templates", "tests", ".github"} for part in base.relative_to(root).parts):
        return None
    for name in ("compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml"):

        composition = _document(base / name, root)
        if isinstance(composition, dict) and isinstance(composition.get("services"), dict):
            return _entry(base, root, kind="container", document={"entrypoint": name, "metadata_inferred": True})
    if _safe_file(base / "Dockerfile", root):
        return _entry(base, root, kind="container", document={"entrypoint": "Dockerfile", "metadata_inferred": True})
    return None


def discover(repo_dir: Path) -> list[dict]:
    root = repo_dir.resolve()
    directories = set()
    patterns = ("range42.yaml", "meta.json", "manifest.json", "bundle_parameters.json", "main.yml", "main.yaml",
                "compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml", "Dockerfile")
    for pattern in patterns:
        for path in root.rglob(pattern):
            if not _safe_file(path, root):
                continue
            parent = path.parent.parent if path.parent.name in {"meta", "tasks"} else path.parent
            directories.add(parent.relative_to(root).as_posix())
    result = []
    for path in sorted(directories):
        entry = detail_at_path(root, path)
        if entry is not None:
            result.append({key: value for key, value in entry.items() if key != "document"})
    return result


def read_readme(repo_dir: Path, path: str) -> str | None:
    root = repo_dir.resolve()
    readme = root / path / "README.md"
    if not _safe_file(readme, root):
        return None
    try:
        return readme.read_text()
    except (OSError, UnicodeError):
        return None
