"""ocr_observation.review_reason: a reading that must be looked at by a person (backlog RD-S2).

A handwritten line is read by two readers, each on its own. When one of them is down or too
slow the line is not held back: it is saved with the one reading it has, and that reading
carries the reason here (``one reading: trocr did not answer``). Resolution then sends any
value resting on it to review. NULL on every other row.

Set when the row is inserted, like the rest of a row: the table stays append-only.

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-09
"""

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ocr_observation ADD COLUMN review_reason text")


def downgrade() -> None:
    op.execute("ALTER TABLE ocr_observation DROP COLUMN review_reason")
