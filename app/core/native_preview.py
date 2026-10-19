"""Bounded native project preview from registered Git objects, without browser PATs."""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import git

from app.core.native_scenarios import inside, invalid

LIMIT = 1024 * 1024
PRIVATE = {'.git', '.env', 'secrets', 'ssh_keys', '.ssh', '.lock'}


def preview_native_files(root: Path, path: str, revision: str) -> dict:
    if not re.fullmatch(r'[a-f0-9]{40}', revision):
        raise invalid('Review an exact native repository commit before opening it')
    inside(root, path)
    with git.Repo(root) as repo:
        if repo.head.commit.hexsha != revision:
            raise invalid('The source branch changed; refresh the catalog and review its new commit')
        try:
            tree = repo.head.commit.tree / path
            if tree.type != 'tree':
                raise ValueError()
            blobs = [blob for blob in tree.traverse() if blob.type == 'blob' and blob.mode in {0o100644, 0o100755}
                     and not any(part.lower() in PRIVATE for part in Path(blob.path).parts)]
        except (KeyError, ValueError):
            raise invalid('Choose a native scenario directory') from None
        main = {f'{path}/main.yml', f'{path}/main.yaml'}
        manifest = f'{path}/manifest/scenario_vms.json'
        names = {blob.path for blob in blobs}
        if not main.intersection(names) or manifest not in names:
            raise invalid('Choose a native scenario with main.yml and manifest/scenario_vms.json')
        blobs.sort(key=lambda blob: (0 if blob.path == manifest else 1 if blob.path in main else 2, blob.path))
        files, total = {}, 0
        for blob in blobs[:128]:
            if blob.size > LIMIT or total + blob.size > LIMIT:
                continue
            data = blob.data_stream.read(LIMIT + 1)
            if len(data) != blob.size:
                raise invalid('Native file size changed while reading its Git object')
            total += len(data)
            try:
                if b'\0' in data:
                    raise UnicodeError()
                content = data.decode('utf-8')
            except UnicodeError:
                content = {'encoding': 'base64', 'content': base64.b64encode(data).decode(),
                           'size': len(data), 'media_type': 'application/octet-stream'}
            files[blob.path] = content
        if manifest not in files or not main.intersection(files):
            raise invalid('Native entrypoints exceed the bounded project preview')
        try:
            document = json.loads(files[manifest])
            if not isinstance(document, dict) or not isinstance(document.get('vms'), list):
                raise ValueError()
        except (ValueError, TypeError):
            raise invalid('Native VM manifest is invalid') from None
        return {'revision': revision, 'path': path, 'files': files,
                'truncated': len(files) != len(blobs)}
