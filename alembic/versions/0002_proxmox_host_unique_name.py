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

    groups: dict[str, list[str]] = {}
    for host_id, name in rows:
        groups.setdefault(name, []).append(host_id)

    for name, ids in groups.items():
        if len(ids) == 1:
            continue
        keeper_id, *loser_ids = ids

        # The id is the oldest row's, because deployments reference it. The
        # connection details are the NEWEST row's: duplicates accumulated one
        # per scenario re-run, so the last registration holds the credentials
        # in force. Keeping the first row wholesale would resurrect a token
        # that may since have been rotated away, and every deployment
        # repointed at it would start failing auth the moment this ran.
        conn.execute(
            sa.text(
                "UPDATE proxmox_hosts SET"
                "   api_url = latest.api_url,"
                "   node_name = latest.node_name,"
                "   token_ref = latest.token_ref,"
                "   token_scope = latest.token_scope,"
                "   default_bridge = latest.default_bridge,"
                "   protected_vmids_override_json ="
                "       latest.protected_vmids_override_json,"
                "   last_health_check_json = latest.last_health_check_json"
                " FROM (SELECT * FROM proxmox_hosts WHERE id = :newest) AS latest"
                " WHERE proxmox_hosts.id = :keeper"
            ),
            {"newest": loser_ids[-1], "keeper": keeper_id},
        )

        for loser_id in loser_ids:
            moved = conn.execute(
                sa.text(
                    "UPDATE deployments SET target_host_id = :keeper"
                    " WHERE target_host_id = :loser"
                ),
                {"keeper": keeper_id, "loser": loser_id},
            ).rowcount
            conn.execute(
                sa.text("DELETE FROM proxmox_hosts WHERE id = :loser"),
                {"loser": loser_id},
            )
            log.info(
                "proxmox_hosts: collapsed duplicate %r (%s) into %s, "
                "repointed %d deployment(s)",
                name, loser_id, keeper_id, moved,
            )
        log.info(
            "proxmox_hosts: %r kept id %s with the connection details from %s",
            name, keeper_id, loser_ids[-1],
        )

    with op.batch_alter_table("proxmox_hosts") as batch:
        batch.create_unique_constraint("uq_proxmox_host_name", ["name"])


def downgrade() -> None:
    # Only the constraint is reversible; the collapsed rows are gone for good.
    with op.batch_alter_table("proxmox_hosts") as batch:
        batch.drop_constraint("uq_proxmox_host_name", type_="unique")
