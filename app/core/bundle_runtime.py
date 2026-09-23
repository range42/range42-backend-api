"""Fingerprint operator-installed bundle code and its explicit dependency profile."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat

from app.core.errors import Range42Error

_ENV_KEYS = {'RANGE42_BUNDLE_DIR', 'ANSIBLE_ROLES_PATH', 'ANSIBLE_COLLECTIONS_PATH',
             'ANSIBLE_COLLECTIONS_PATHS', 'ANSIBLE_FILTER_PLUGINS', 'ANSIBLE_LIBRARY', 'ANSIBLE_CONFIG'}
_IGNORED = {'.git', '__pycache__', '.pytest_cache', '.ruff_cache'}


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def runtime_error(message: str) -> Range42Error:
    return Range42Error(code='BUNDLE_RUNTIME_UNAVAILABLE', error='bundle_runtime_unavailable', status=409,
                        message=f'Bundle runtime {message}')


def runtime_environment() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if value and
            (key in _ENV_KEYS or key.startswith(('RANGE42_INVENTORY__', 'RANGE42_GITDIR__')))}


def tree_digest(root: Path, *, max_files: int = 100000, max_bytes: int = 1024 * 1024 * 1024) -> str:
    """Hash source bytes, executable bits and contained link targets, never caches."""
    root = root.absolute()
    if root.is_symlink() or not root.exists():
        raise ValueError('runtime root must be an existing real file or directory')
    boundary = root.resolve() if root.is_dir() else root.parent.resolve()
    digest = hashlib.sha256()
    total = count = 0
    paths = sorted(root.rglob('*')) if root.is_dir() else [root]
    for path in paths:
        relative = path.relative_to(root).as_posix() if path != root else '.'
        if any(part in _IGNORED for part in Path(relative).parts):
            continue
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        count += 1
        if count > max_files:
            raise ValueError('runtime tree exceeds file limit')
        digest.update(canonical([relative, bool(info.st_mode & 0o111)]))
        if stat.S_ISLNK(info.st_mode):
            if not path.resolve(strict=True).is_relative_to(boundary):
                raise ValueError('runtime symbolic link escapes its component')
            digest.update(b'link:' + os.readlink(path).encode())
        elif stat.S_ISREG(info.st_mode):
            total += info.st_size
            if total > max_bytes:
                raise ValueError('runtime tree exceeds size limit')
            digest.update(b'file:' + str(info.st_size).encode() + b':')
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
        else:
            raise ValueError('runtime tree contains a special file')
    return digest.hexdigest()


def _covered(environment: dict[str, str], roots: list[Path]) -> None:
    for value in environment.values():
        for item in value.split(os.pathsep):
            path = Path(item)
            if not path.is_absolute() or not path.exists():
                raise ValueError('runtime environment paths must exist and be absolute')
            resolved = path.resolve()
            if not any(resolved == root or resolved.is_relative_to(root) for root in roots):
                # A repository-root environment may name the common parent of
                # multiple components; it cannot authorize an unrelated root.
                if not all(root.is_relative_to(resolved) for root in roots):
                    raise ValueError('runtime environment path is outside declared components')


def build_runtime_manifest(components: dict[str, Path]) -> dict:
    if not components or len(components) > 16:
        raise ValueError('declare between one and sixteen runtime components')
    records = {}
    for name, root in sorted(components.items()):
        root = root.absolute()
        marker = root / '.range42-revision' if root.is_dir() else None
        revision = marker.read_text().strip() if marker and marker.is_file() else 'content-only'
        records[name] = {'root': str(root), 'revision': revision, 'sha256': tree_digest(root)}
    environment = runtime_environment()
    if not environment.get('RANGE42_BUNDLE_DIR') or not environment.get('ANSIBLE_ROLES_PATH'):
        raise ValueError('runtime requires RANGE42_BUNDLE_DIR and ANSIBLE_ROLES_PATH')
    _covered(environment, [Path(item['root']).resolve() for item in records.values()])
    return {'version': 1, 'components': records, 'environment': environment}


def runtime_snapshot() -> tuple[dict, str]:
    try:
        path = Path(os.environ['RANGE42_BUNDLE_RUNTIME_MANIFEST'])
        if not path.is_file() or path.stat().st_size > 65536:
            raise ValueError('invalid runtime manifest')
        profile = json.loads(path.read_text())
        if profile.get('version') != 1 or not isinstance(profile.get('components'), dict):
            raise ValueError('unsupported runtime manifest')
        if profile.get('environment') != runtime_environment():
            raise ValueError('runtime environment changed')
        current = build_runtime_manifest({name: Path(item['root']) for name, item in profile['components'].items()})
        if current != profile:
            raise ValueError('runtime source content changed')
        return profile, hashlib.sha256(canonical(profile)).hexdigest()
    except (OSError, ValueError, KeyError, TypeError):
        raise runtime_error('profile is missing or has changed; install a matching release and regenerate its manifest') from None


def dependencies(profile: dict) -> list[dict]:
    return [{'name': name, 'revision': value['revision'], 'sha256': value['sha256']}
            for name, value in sorted(profile['components'].items())]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--component', action='append', required=True, metavar='NAME=PATH')
    args = parser.parse_args()
    components = dict(item.split('=', 1) for item in args.component)
    profile = build_runtime_manifest({name: Path(path) for name, path in components.items()})
    args.output.write_text(json.dumps(profile, indent=2) + '\n')


if __name__ == '__main__':
    main()
