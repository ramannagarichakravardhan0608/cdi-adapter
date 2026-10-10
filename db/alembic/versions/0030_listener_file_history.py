"""What happened to a file, in order: ``listener_file.history`` (backlog LS-S5).

The listener runs files in batches of three. Each batch has an id, and **every file of the batch
carries it**: when a file is taken into a batch the listener appends
``{"event": "batch", "batch_id": ..., "position": n, "size": m, "at": ...}`` to its history, and
when the file finishes it appends the outcome with ``seconds_from_drop`` - the time from the
moment the listener first saw the file to the moment its result was saved - which is what the
load test (LS-S10) reads.

An append-only list on the row, not a table of its own: it is read with the row, and a file
has a handful of events in its life.

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-09
"""

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE listener_file
            ADD COLUMN history jsonb NOT NULL DEFAULT '[]'::jsonb
                CONSTRAINT listener_file_history_is_a_list CHECK (jsonb_typeof(history) = 'array')
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE listener_file DROP COLUMN history")
