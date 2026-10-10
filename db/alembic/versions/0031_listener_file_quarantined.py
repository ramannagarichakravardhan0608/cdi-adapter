"""A file that has failed as often as it may is ``quarantined`` (backlog LS-S6).

After reading, a file is filed by outcome: ``success``, ``error``, or - when its runs are used
up (``CDI_LISTENER_MAX_RUNS``) - ``quarantine``, where it stays until a person looks at it.
``listener_file.state`` gains the value for that. Every move is also written to the row's
``history`` (0030), which needs no change here.

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-09
"""

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

BEFORE = "('seen', 'processing', 'succeeded', 'failed', 'refused', 'duplicate', 'ignored')"
AFTER = BEFORE[:-1] + ", 'quarantined')"


def _states(allowed: str) -> None:
    op.execute("ALTER TABLE listener_file DROP CONSTRAINT listener_file_state_check")
    op.execute("ALTER TABLE listener_file ADD CONSTRAINT listener_file_state_check "
               f"CHECK (state IN {allowed})")  # fmt: skip


def upgrade() -> None:
    _states(AFTER)


def downgrade() -> None:
    op.execute("UPDATE listener_file SET state = 'failed' WHERE state = 'quarantined'")
    _states(BEFORE)
