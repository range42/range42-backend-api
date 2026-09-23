"""Workspace bootstrap + local-FS invariant.

Each deployment owns one workspace at
~/range42.config/<CODENAME>-<SCENARIO>/ with a fixed layout:
  inventory/  secrets/  ssh_keys/  bin/  runner/  events.jsonl  redactions.jsonl

Refuses non-local filesystems (nfs, cifs, fuse) with WORKSPACE_NON_LOCAL_FS.
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings


# "overlayfs" and "overlay" are the same filesystem: `stat -f -c %T` reports
# the former on some kernels, the latter elsewhere. Both must be accepted or
# the backend refuses to create a workspace whenever its root sits on the
# container's own layer rather than a bind mount.
_LOCAL_FS = {"ext2", "ext3", "ext4", "xfs", "btrfs", "tmpfs", "zfs",
             "f2fs", "overlay", "overlayfs", "apfs", "hfs", "ntfs"}


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


def _template_files(template: Path) -> dict[Path, bytes]:
    """Read a private operator template completely before changing a workspace."""
    def directory(path: Path) -> None:
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
            raise ValueError("template directories must be private real directories")

    def read(path: Path) -> bytes:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 1024 * 1024:
                raise ValueError("template files must be private regular files under 1 MiB")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                return stream.read(1024 * 1024 + 1)
        finally:
            os.close(fd)

    if template.absolute() != template.resolve(strict=True):
        raise ValueError("template path cannot contain symbolic links")
    directory(template)
    directory(template / "secrets")
    result = {Path("secrets") / name: read(template / "secrets" / name)
              for name in ("default_vault.yml", "vault_pass.txt")}
    if any(not content.strip() for content in result.values()):
        raise ValueError("template vault and password must not be empty")
    ssh = template / "ssh_keys"
    if ssh.exists() or ssh.is_symlink():
        directory(ssh)
        known_hosts = ssh / "known_hosts"
        if known_hosts.exists() or known_hosts.is_symlink():
            result[Path("ssh_keys/known_hosts")] = read(known_hosts)
        for name in ("backend_keys", "jump_keys", "student_keys"):
            key_dir = ssh / name
            if not key_dir.exists() and not key_dir.is_symlink():
                continue
            directory(key_dir)
            for path in sorted(key_dir.rglob("*")):
                if path.is_dir() and not path.is_symlink():
                    directory(path)
                else:
                    result[path.relative_to(template)] = read(path)
                if len(result) > 256:
                    raise ValueError("template contains too many credential files")
    return result


def safe_workspace_path(root: Path, codename: str, scenario_label: str) -> Path:
    """Validate names and containment before touching deployment files."""
    for value in (codename, scenario_label):
        if not re.fullmatch(r"[A-Za-z0-9_-]+(?:[./][A-Za-z0-9_-]+)*", value):
            raise WorkspaceError("WORKSPACE_PATH_INVALID", "Invalid workspace name")
    if '/' in codename or '/' in scenario_label:
        raise WorkspaceError("WORKSPACE_PATH_INVALID", "Workspace name must be a single directory")
    root = root.resolve()
    target = root / f"{codename}-{scenario_label}"
    if not target.resolve().is_relative_to(root):
        raise WorkspaceError("WORKSPACE_PATH_INVALID", "Workspace escapes configured root")
    # Reject existing symlinks inside the workspace as well as in its parents.
    for part in (target, *target.parents):
        if part == root:
            break
        if part.is_symlink():
            raise WorkspaceError("WORKSPACE_PATH_INVALID", "Workspace contains a symlink")
    for sub in ("inventory", "secrets", "ssh_keys", "bin", "runner", "events.jsonl", "redactions.jsonl"):
        if (target / sub).is_symlink():
            raise WorkspaceError("WORKSPACE_PATH_INVALID", "Workspace contains a symlink")
    return target


@dataclass
class Workspace:
    path: Path
    codename: str
    scenario_label: str

    @classmethod
    def create(cls, *, codename: str, scenario_label: str,
               workspace_root: Path | None = None,
               inherit_template: bool = True) -> "Workspace":
        root = Path(workspace_root or settings.workspace_root)
        target = safe_workspace_path(root, codename, scenario_label)
        root.mkdir(parents=True, exist_ok=True)
        fs = detect_fs_type(root)
        if not is_local_fs(fs):
            raise WorkspaceError(
                code="WORKSPACE_NON_LOCAL_FS",
                message=f"Workspace root is on non-local filesystem: {fs}",
            )
        dirname = f"{codename}-{scenario_label}"
        if Path(dirname).name != dirname:
            raise WorkspaceError("WORKSPACE_PATH_INVALID", "Workspace name must be a single directory")
        subdirs = ("inventory", "secrets", "ssh_keys", "bin", "runner")
        for path in (target, *(target / name for name in subdirs)):
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                raise WorkspaceError("WORKSPACE_PATH_INVALID", "Workspace directories cannot be symbolic links or files")
        # Credentials form a coherent set. Never combine a user's vault/password
        # or keys with installation defaults encrypted using a different password.
        has_credentials = any(path.exists() and any(path.iterdir())
                              for path in (target / "secrets", target / "ssh_keys"))
        files: dict[Path, bytes] = {}
        configured = os.getenv("RANGE42_WORKSPACE_TEMPLATE_DIR", "").strip()
        if inherit_template and configured and not has_credentials:
            try:
                files = _template_files(Path(configured))
            except (OSError, ValueError):
                raise WorkspaceError(
                    "WORKSPACE_TEMPLATE_INVALID",
                    "Workspace credential template must contain private vault/SSH files and directories, without symbolic links",
                ) from None
        created_dirs: list[Path] = []
        created_files: list[tuple[Path, int, int]] = []

        def make_dir(path: Path) -> None:
            if path.is_symlink():
                raise ValueError("workspace directory is a symbolic link")
            if not path.exists():
                path.mkdir(mode=0o700)
                created_dirs.append(path)
            path.chmod(0o700)

        def write_new(path: Path, content: bytes) -> None:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                info = os.fstat(fd)
                created_files.append((path, info.st_dev, info.st_ino))
                pending = memoryview(content)
                while pending:
                    count = os.write(fd, pending)
                    if count <= 0:
                        raise OSError("incomplete credential write")
                    pending = pending[count:]
                os.fsync(fd)
            finally:
                os.close(fd)

        try:
            make_dir(target)
            for name in subdirs:
                make_dir(target / name)
            for relative, content in files.items():
                for parent in reversed(relative.parents):
                    if parent != Path("."):
                        make_dir(target / parent)
                # O_EXCL also fails closed if another creator wrote credentials
                # after the initial empty-workspace check; nothing is overwritten.
                write_new(target / relative, content)
            for name in ("events.jsonl", "redactions.jsonl"):
                path = target / name
                if path.is_symlink() or (path.exists() and not path.is_file()):
                    raise ValueError("workspace event file is not a regular file")
                if not path.exists():
                    write_new(path, b"")
        except (OSError, ValueError):
            for path, device, inode in reversed(created_files):
                try:
                    info = path.lstat()
                    if (info.st_dev, info.st_ino) == (device, inode):
                        path.unlink()
                except OSError:
                    pass
            for path in reversed(created_dirs):
                try:
                    path.rmdir()  # Never remove existing or nonempty user directories.
                except OSError:
                    pass
            raise WorkspaceError("WORKSPACE_CREATE_FAILED", "Could not securely initialize the workspace") from None
        return cls(path=target, codename=codename, scenario_label=scenario_label)

    @classmethod
    def resolve(cls, *, codename: str, scenario_label: str,
                workspace_root: Path | None = None) -> "Workspace":
        root = Path(workspace_root or settings.workspace_root)
        return cls(path=safe_workspace_path(root, codename, scenario_label),
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
