"""The mobile number and the token number typed at the desk, kept with the upload.

The upload screen takes two more optional boxes beside the ABHA number (owner, 2026-10-09): the
patient's **mobile number** and the clinic's **token number** for the visit. Both are stored on
the job exactly as the ABHA number is, so the desk and the reviewer can tell which visit an
upload belongs to. Neither is used to match a patient here: finding an existing patient by
mobile is the "Existing patient" box.

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-09
"""

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE web_job
            ADD COLUMN mobile   text CHECK (mobile IS NULL OR mobile ~ '^[6-9][0-9]{9}$'),
            ADD COLUMN token_no text CHECK (token_no IS NULL OR length(token_no) BETWEEN 1 AND 20)
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE web_job DROP COLUMN token_no, DROP COLUMN mobile")
