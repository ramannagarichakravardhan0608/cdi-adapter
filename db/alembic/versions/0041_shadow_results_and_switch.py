"""Shadow answers, and the champion switch in the rollout log (backlog SW-S5).

Additive only:

* ``shadow_result``: what a CANDIDATE model answered for a document while it ran in shadow
  mode - beside the real result, never in it. Nobody's screen or result reads this table; the
  shadow report compares it with the champion's answer. It holds what was read off a patient's
  page, so it goes with the document: ``ON DELETE CASCADE``, like every other reading;
* ``model_rollout.action`` may also be ``switch``: the champion changed on purpose (after a
  contest, SW-S4), with the documents already running left to finish on the model they started
  on.

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-14
"""

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None

ACTIONS = "('deploy', 'canary', 'set_percent', 'promote', 'rollback'{})"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE shadow_result (
            id               bigserial PRIMARY KEY,
            document_id      uuid NOT NULL REFERENCES source_document (id) ON DELETE CASCADE,
            role             text NOT NULL DEFAULT 'extraction',
            model_id         text NOT NULL,
            revision         text,
            registered_as    text,
            gateway          text,
            champion_model   text,
            champion_version text,
            prompt_version   text,
            answer           jsonb,
            error            text,
            seconds          double precision,
            agreement        jsonb,
            created_at       timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_shadow_result_model ON shadow_result (model_id, created_at)")
    op.execute("CREATE INDEX ix_shadow_result_document ON shadow_result (document_id)")
    op.execute("ALTER TABLE model_rollout DROP CONSTRAINT model_rollout_action_check")
    op.execute(
        "ALTER TABLE model_rollout ADD CONSTRAINT model_rollout_action_check CHECK (action IN "
        + ACTIONS.format(", 'switch'")
        + ")"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE model_rollout DROP CONSTRAINT model_rollout_action_check")
    # a switch row cannot go back to a table that does not know the word: it reads as a deploy
    op.execute("ALTER TABLE model_rollout DISABLE TRIGGER model_rollout_no_rewrite")
    op.execute("UPDATE model_rollout SET action = 'deploy' WHERE action = 'switch'")
    op.execute("ALTER TABLE model_rollout ENABLE TRIGGER model_rollout_no_rewrite")
    op.execute(
        "ALTER TABLE model_rollout ADD CONSTRAINT model_rollout_action_check CHECK (action IN "
        + ACTIONS.format("")
        + ")"
    )
    op.execute("DROP TABLE IF EXISTS shadow_result")
