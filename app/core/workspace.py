"""Workspace bootstrap + local-FS invariant.

Each deployment owns one workspace at
~/range42.config/<CODENAME>-<SCENARIO>/ with a fixed layout:
  inventory/  secrets/  ssh_keys/  bin/  runner/  events.jsonl  redactions.jsonl

Refuses non-local filesystems (nfs, cifs, fuse) with WORKSPACE_NON_LOCAL_FS.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings


_LOCAL_FS = {"ext2", "ext3", "ext4", "xfs", "btrfs", "tmpfs", "zfs",
             "f2fs", "overlay", "apfs", "hfs", "ntfs"}


class WorkspaceError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def detect_fs_type(path: Path) -> str:
    p = Path(path)
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p = p.parent
    try:
        out = subprocess.check_output(["stat", "-f", "-c", "%T", str(p)],
                                      text=True, stderr=subprocess.DEVNULL).strip()
        if out:
            return out
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass
    # Fallback: parse /proc/mounts
    try:
        mounts = Path("/proc/mounts").read_text().splitlines()
    except OSError:
        return "unknown"
    best = ("unknown", 0)
    path_str = str(p)
    for line in mounts:
        parts = line.split()
        if len(parts) < 3:
            continue
        mount_point, fs = parts[1], parts[2]
        if path_str == mount_point or path_str.startswith(mount_point.rstrip("/") + "/"):
            if len(mount_point) > best[1]:
                best = (fs, len(mount_point))
    return best[0]


def is_local_fs(name: str) -> bool:
    n = name.lower()
    if n.startswith("fuse"):
        return False
    if n in ("nfs", "nfs3", "nfs4", "cifs", "smb", "smbfs", "sshfs"):
        return False
    # `stat -f -c %T` can return compound labels like "ext2/ext3" for ext4
    # or "msdos/vfat"; accept if any token is a known local FS.
    tokens = [t for t in n.replace(",", "/").split("/") if t]
    if any(t in _LOCAL_FS for t in tokens):
        return True
    return n in _LOCAL_FS or n == "tmpfs"


@dataclass
class Workspace:
    path: Path
    codename: str
    scenario_label: str

    @classmethod
    def create(cls, *, codename: str, scenario_label: str,
               workspace_root: Path | None = None) -> "Workspace":
        root = Path(workspace_root or settings.workspace_root)
        root.mkdir(parents=True, exist_ok=True)
        fs = detect_fs_type(root)
        if not is_local_fs(fs):
            raise WorkspaceError(
                code="WORKSPACE_NON_LOCAL_FS",
                message=f"Workspace root is on non-local filesystem: {fs}",
            )
        dirname = f"{codename}-{scenario_label}"
        target = root / dirname
        for sub in ("inventory", "secrets", "ssh_keys", "bin", "runner"):
            (target / sub).mkdir(parents=True, exist_ok=True)
        # Touch events+redactions files with sentinels.
        (target / "events.jsonl").touch(exist_ok=True)
        (target / "redactions.jsonl").touch(exist_ok=True)
        return cls(path=target, codename=codename, scenario_label=scenario_label)

    @classmethod
    def resolve(cls, *, codename: str, scenario_label: str,
                workspace_root: Path | None = None) -> "Workspace":
        root = Path(workspace_root or settings.workspace_root)
        return cls(path=root / f"{codename}-{scenario_label}",
                   codename=codename, scenario_label=scenario_label)


def shred_envvars(path: Path) -> None:
    """Overwrite then unlink a file containing secrets.

    Defense-in-depth: prevents the snapshotted PAT / vault pass from sitting
    on disk after the attempt completes. Not a true cryptographic shred —
    if the filesystem stores backups (CoW snapshots), the file may persist.

    No-op if the file doesn't exist (so cleanup is idempotent).
    """
    if not path.is_file():
        return
    try:
        size = path.stat().st_size
        with path.open("rb+") as f:
            f.write(b"\x00" * size)
            f.flush()
            os.fsync(f.fileno())
        path.unlink()
    except OSError:
        # Best-effort: if shredding fails for any reason, still try to unlink
        try:
            path.unlink()
        except OSError:
            pass
