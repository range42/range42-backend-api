"""Workspace side effects for a deployment create, with one durability rule.

Creating a deployment touches three things that must agree: the database row,
the vault password on disk, and (best effort) a provisioned Proxmox token.
Four rounds of review found four different ways to get that ordering wrong —
a duplicate clobbering a live workspace, a committed row with no password, a
misreported constraint, a secret outliving the row it belonged to.

The rule this module exists to enforce is:

    the vault password is durable if and only if the deployment row is.

`vault_seed` is a context manager, so the caller cannot forget the other half:
whatever it wraps either succeeds, or the workspace goes back to how it was.

    with vault_seed(ws.path, payload.secrets):
        await session.commit()
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger(__name__)

VAULT_PASS_RELPATH = ("secrets", "vault_pass.txt")


class VaultSeedError(Exception):
    """The vault password could not be written to the workspace."""

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"could not write {path}: {reason}")
        self.path = path
        self.reason = reason


@dataclass
class _Seed:
    """Applied state, kept so it can be undone."""

    path: Path
    prior: str | None = None
    applied: bool = False
    _existed: bool = field(default=False, repr=False)

    def revert(self) -> None:
        """Put the workspace back. Best-effort: never masks the real failure."""
        if not self.applied:
            return
        try:
            if self._existed:
                self.path.write_text(self.prior or "")
            else:
                self.path.unlink(missing_ok=True)
        except OSError as e:
            logger.warning("could not revert workspace vault password",
                           path=str(self.path), error=str(e))
        finally:
            self.applied = False


def vault_pass_path(workspace: Path) -> Path:
    """Where deploy_trigger looks for the vault password."""
    return workspace.joinpath(*VAULT_PASS_RELPATH)


@contextmanager
def vault_seed(workspace: Path,
               secrets: Mapping[str, str] | None) -> Iterator[Path | None]:
    """Write the vault password for the duration of the wrapped block.

    Yields the path written, or ``None`` when the payload carried no password —
    in which case an operator-seeded file is left untouched, since a create
    that omits the field must not erase one that supplied it.

    If the wrapped block raises, the previous state is restored: the file is
    removed when there was none, or its old contents put back. That is what
    keeps a rolled-back deployment from leaving a secret behind for the next
    create — which, supplying no password of its own, would silently adopt it.

    :raises VaultSeedError: the write itself failed; nothing was changed that
        the caller needs to undo.
    """
    secret = (secrets or {}).get("vault_password")
    if not secret:
        yield None
        return

    path = vault_pass_path(workspace)
    seed = _Seed(path=path)
    try:
        seed._existed = path.is_file()
        seed.prior = path.read_text() if seed._existed else None
        path.parent.mkdir(parents=True, exist_ok=True)
        # chmod before the content so the secret is never briefly world-readable.
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)
        path.write_text(secret)
        seed.applied = True
    except OSError as e:
        seed.revert()
        raise VaultSeedError(path, str(e)) from e

    try:
        yield path
    except BaseException:
        seed.revert()
        raise


def provision_host_token(workspace: Path, host_id: str,
                         secrets: Mapping[str, str] | None) -> bool:
    """Provision a Proxmox token into the workspace vault, if we can.

    Deliberately best-effort and deliberately *outside* `vault_seed`: it talks
    to Proxmox rather than the workspace's own durability, and preflight is the
    source of truth for whether the credential actually works.

    :returns: True when a token was provisioned.
    """
    if not secrets or "proxmox_token" not in secrets:
        return False
    try:
        from app.core.proxmox_secrets import provision_proxmox_token
        from app.core.vault import VaultManager

        vault_file = VaultManager().vault_file
        if not vault_file or not Path(vault_file).exists():
            return False
        provision_proxmox_token(
            workspace=workspace,
            host_id=host_id,
            api_url="",
            token_id="",
            token_secret=secrets["proxmox_token"],
            vault_password_file=Path(vault_file),
        )
        return True
    except Exception as e:  # noqa: BLE001 — preflight is the source of truth
        logger.warning("proxmox token provisioning skipped",
                       host_id=host_id, error=str(e))
        return False
