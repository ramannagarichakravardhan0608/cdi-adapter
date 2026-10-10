"""Which result a dropped file became, and where the file came from (backlog LS-S8).

A file read from the watched folder ends as the same database rows and the same result JSON as
an upload from the screen. Three columns on ``listener_file`` tie the two together:

* ``document_id`` - the stored document this file became (``source_document``). It is the link
  from a dropped file to its result, and the way a result finds out it came from a folder. The
  same bytes dropped twice, or dropped after being uploaded, point at the one document.
* ``original_name`` - the file's name as it was dropped. ``name`` follows the file as it moves
  (a second ``rx.pdf`` in ``success`` is kept as ``rx.1.pdf``); this one never changes, so a
  person can find a result by the name they know.
* ``folder`` - the watched folder as a person would say it (a path), beside ``source_id``,
  which on a cloud drive is made of ids.

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-09
"""

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE listener_file
            ADD COLUMN document_id   uuid REFERENCES source_document (id) ON DELETE SET NULL,
            ADD COLUMN original_name text,
            ADD COLUMN folder        text
        """
    )
    op.execute("UPDATE listener_file SET original_name = name WHERE original_name IS NULL")
    op.execute("CREATE INDEX ix_listener_file_document ON listener_file (document_id) "
               "WHERE document_id IS NOT NULL")  # fmt: skip
    op.execute("CREATE INDEX ix_listener_file_original_name "
               "ON listener_file (lower(original_name))")  # fmt: skip


def downgrade() -> None:
    op.execute("DROP INDEX ix_listener_file_original_name")
    op.execute("DROP INDEX ix_listener_file_document")
    op.execute("ALTER TABLE listener_file DROP COLUMN folder, DROP COLUMN original_name, "
               "DROP COLUMN document_id")  # fmt: skip
