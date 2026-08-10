"""dedupe proxmox_hosts, then enforce a unique name

Until now nothing stopped a second host row under an existing name, and the
deploy bundle re-POSTs the same name on every scenario run — so field
databases carry one duplicate per re-run. The duplicates are not inert:
``deployments.target_host_id`` points at whichever one existed when the
deployment was created.

Collapsing keys on ``added_at``: the earliest row under a name is the one the
first deploy registered, so it is the row most likely to be referenced and the
one whose id callers may have recorded. Deployments on the later duplicates are
repointed at it before those rows are deleted, so nothing is left dangling.

Revision ID: 0002_proxmox_host_unique_name
Revises: 0001_v1_initial
Create Date: 2026-08-10
"""
import logging

import sqlalchemy as sa
from alembic import op

revision = '0002_proxmox_host_unique_name'
down_revision = '0001_v1_initial'
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    conn = op.get_bind()

    # added_at first, id as a deterministic tie-break for rows registered
    # within the same clock tick.
    rows = conn.execute(
        sa.text("SELECT id, name FROM proxmox_hosts ORDER BY name, added_at, id")
    ).fetchall()

    keepers: dict[str, str] = {}
    for host_id, name in rows:
        keeper_id = keepers.setdefault(name, host_id)
        if keeper_id == host_id:
            continue

        moved = conn.execute(
            sa.text(
                "UPDATE deployments SET target_host_id = :keeper"
                " WHERE target_host_id = :loser"
            ),
            {"keeper": keeper_id, "loser": host_id},
        ).rowcount
        conn.execute(
            sa.text("DELETE FROM proxmox_hosts WHERE id = :loser"),
            {"loser": host_id},
        )
        log.info(
            "proxmox_hosts: collapsed duplicate %r (%s) into %s, "
            "repointed %d deployment(s)",
            name, host_id, keeper_id, moved,
        )

    with op.batch_alter_table("proxmox_hosts") as batch:
        batch.create_unique_constraint("uq_proxmox_host_name", ["name"])


def downgrade() -> None:
    # Only the constraint is reversible; the collapsed rows are gone for good.
    with op.batch_alter_table("proxmox_hosts") as batch:
        batch.drop_constraint("uq_proxmox_host_name", type_="unique")
