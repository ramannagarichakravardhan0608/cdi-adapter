"""clinical_fact.fact_type: 'preparation' (backlog EX-S4).

A lab preparation instruction - "fasting 12 hrs", "morning sample", "stop biotin" - is saved
like every other value: one fact, with its proof and status. ``local_text`` is the original
words (always kept); ``value_num`` / ``value_unit_ucum`` the number when one is written
(12, h); ``value_text`` what it applies to - ``all``, ``unclear`` or ``tests: FBS | HbA1c``.
Its type is read from the words by the phrase file, not stored.

Revision ID: 0038
Revises: 0037
Create Date: 2026-10-12
"""

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None

_TYPES = (
    "'condition', 'symptom', 'finding', 'lab_result', 'vital_sign', 'procedure', 'medication', "
    "'allergy', 'immunization', 'diagnostic_report', 'advice', 'care_plan', 'referral', "
    "'document', 'lab_order', 'patient_detail', 'doctor_detail'"
)


def upgrade() -> None:
    op.execute("ALTER TABLE clinical_fact DROP CONSTRAINT clinical_fact_fact_type_check")
    op.execute(
        "ALTER TABLE clinical_fact ADD CONSTRAINT clinical_fact_fact_type_check "
        f"CHECK (fact_type IN ({_TYPES}, 'preparation'))"
    )


def downgrade() -> None:
    op.execute("DELETE FROM clinical_fact WHERE fact_type = 'preparation'")
    op.execute("ALTER TABLE clinical_fact DROP CONSTRAINT clinical_fact_fact_type_check")
    op.execute(
        "ALTER TABLE clinical_fact ADD CONSTRAINT clinical_fact_fact_type_check "
        f"CHECK (fact_type IN ({_TYPES}))"
    )
