"""One row per file the listener has seen (backlog LS-S3; ``listener/ledger.py``).

The watched folder is a shared place: half-uploaded files, editors' lock files and the same
picture dropped twice all turn up in it. ``listener_file`` is what lets the listener pick up
only finished, new, allowed files, and say what it did with every other one:

* ``stable_polls`` - how many looks in a row the file's ``size`` and ``fingerprint`` (the
  drive's version tag, or its modified date) have stayed the same. A file is taken only at
  ``CDI_LISTENER_STABLE_POLLS``;
* ``sha256`` - the bytes of a file that went through, so the same bytes dropped again are
  recognised (``duplicate``, pointing at the first with ``duplicate_of``) instead of read twice;
* ``lease_owner`` / ``lease_until`` - the lease: one worker has the file until it finishes or
  the lease runs out;
* ``state = 'ignored'`` with a ``reason`` - a file that is never taken (a type that is not
  allowed, a temporary file), kept so a person can see it was not simply lost;
* ``state = 'refused'`` - a data error (too large, too many pages), never retried;
* ``source_id`` - the drive the file is on, so rows of another drive are left alone after the
  drive setting changes.

**Numbering.** This is 0029 on purpose: ``dev_main`` already has a 0028 (continuity views) that
this branch does not. Until the branches are joined this revision follows 0027; when they are,
change ``down_revision`` to ``"0028"`` so the chain is one line again.

Revision ID: 0029
Revises: 0027
Create Date: 2026-10-08
"""

from alembic import op

revision = "0029"
down_revision = "0027"
branch_labels = None
depends_on = None

STATES = "('seen', 'processing', 'succeeded', 'failed', 'refused', 'duplicate', 'ignored')"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE listener_file (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            source_id     text NOT NULL,
            file_id       text NOT NULL,
            name          text NOT NULL,
            size          bigint,
            fingerprint   text,
            modified_at   timestamptz,
            stable_polls  integer NOT NULL DEFAULT 1 CHECK (stable_polls >= 1),
            state         text NOT NULL DEFAULT 'seen' CHECK (state IN {STATES}),
            kind          text,
            reason        text,
            sha256        text CHECK (sha256 IS NULL OR sha256 ~ '^[0-9a-f]{{64}}$'),
            job_id        text,
            duplicate_of  uuid REFERENCES listener_file (id) ON DELETE SET NULL,
            lease_owner   text,
            lease_until   timestamptz,
            first_seen_at timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now(),
            -- one row per file on a drive
            CONSTRAINT listener_file_one_per_file UNIQUE (source_id, file_id),
            -- a lease has an owner and an end, or neither
            CONSTRAINT listener_file_lease_complete CHECK (
                (lease_owner IS NULL) = (lease_until IS NULL)),
            -- an ignored file says why
            CONSTRAINT listener_file_ignored_has_reason CHECK (
                state <> 'ignored' OR reason IS NOT NULL)
        )
        """
    )
    op.execute("CREATE INDEX ix_listener_file_sha256 ON listener_file (sha256) "
               "WHERE state = 'succeeded'")  # fmt: skip
    op.execute(
        "CREATE INDEX ix_listener_file_state ON listener_file (source_id, state, updated_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE listener_file")
