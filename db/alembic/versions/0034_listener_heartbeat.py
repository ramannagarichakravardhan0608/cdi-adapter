"""The listener says it is alive: one row per watched folder (backlog LS-S9).

After every look at its folder the listener writes when it looked, whether the look succeeded,
how many files are still waiting and how long the oldest of them has waited. That row is what
the health view shows and what the "no poll for 5 minutes" alert reads - so both work
whichever machine the listener runs on, and survive a restart with the rest of the database.

``last_ok_at`` moves only on a look that succeeded; ``last_poll_at`` on every look. A listener
that is running but cannot reach its pipeline keeps polling and stops being "ok", with the
reason in ``reason``.

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-09
"""

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE listener_heartbeat (
            source_id         text PRIMARY KEY,
            worker            text NOT NULL,
            last_poll_at      timestamptz NOT NULL DEFAULT now(),
            last_ok_at        timestamptz,
            waiting           integer NOT NULL DEFAULT 0 CHECK (waiting >= 0),
            oldest_waiting_at timestamptz,
            reason            text
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE listener_heartbeat")
