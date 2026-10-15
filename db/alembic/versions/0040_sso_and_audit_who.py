"""Single sign-on accounts, and who / from where in the audit trail (backlog ENT-S2).

Additive only:

* ``web_user.password_hash`` may be NULL: a person who signs in through the organisation's
  identity system has NO password here ("no passwords stored by us"). A local account still
  has one, and the check keeps every account one or the other - never neither.
* ``web_user.provider`` / ``subject``: which identity system the account comes from and its
  never-changing id there (the ``sub`` claim). Unique together: one person, one account.
* ``audit_log.actor_id`` / ``source_addr``: the user's id (the user name alone can be given to
  someone else later) and the address the request came from.

Revision ID: 0040
Revises: 0039
Create Date: 2026-10-13
"""

from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE web_user ALTER COLUMN password_hash DROP NOT NULL")
    op.execute("ALTER TABLE web_user ADD COLUMN provider text")
    op.execute("ALTER TABLE web_user ADD COLUMN subject text")
    op.execute(
        "ALTER TABLE web_user ADD CONSTRAINT ck_web_user_one_way_in CHECK "
        "((password_hash IS NOT NULL) <> (subject IS NOT NULL AND provider IS NOT NULL))"
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_web_user_subject ON web_user (provider, subject) "
        "WHERE subject IS NOT NULL"
    )
    op.execute("ALTER TABLE audit_log ADD COLUMN actor_id text")
    op.execute("ALTER TABLE audit_log ADD COLUMN source_addr text")
    op.execute("CREATE INDEX ix_audit_log_actor_at ON audit_log (actor, at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_audit_log_actor_at")
    op.execute("ALTER TABLE audit_log DROP COLUMN IF EXISTS source_addr")
    op.execute("ALTER TABLE audit_log DROP COLUMN IF EXISTS actor_id")
    op.execute("DROP INDEX IF EXISTS ux_web_user_subject")
    op.execute("ALTER TABLE web_user DROP CONSTRAINT IF EXISTS ck_web_user_one_way_in")
    # accounts that have no password cannot go back to a table that demands one
    op.execute("DELETE FROM web_user WHERE password_hash IS NULL")
    op.execute("ALTER TABLE web_user DROP COLUMN IF EXISTS subject")
    op.execute("ALTER TABLE web_user DROP COLUMN IF EXISTS provider")
    op.execute("ALTER TABLE web_user ALTER COLUMN password_hash SET NOT NULL")
