"""clinical_fact.fact_type: 'patient_detail' (backlog EX-S1).

The patient's details read from a prescription - name, age, sex, date of birth, phone, address,
hospital number, ABHA number - are saved like every other value: one fact each, with its proof
(the part of the picture) and its status. ``local_text`` is the detail's name ("phone"),
``value_text`` the value exactly as written. They are never promoted into the patient record:
creating or merging patients is not part of this.

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-12
"""

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None

_TYPES = (
    "'condition', 'symptom', 'finding', 'lab_result', 'vital_sign', 'procedure', 'medication', "
    "'allergy', 'immunization', 'diagnostic_report', 'advice', 'care_plan', 'referral', "
    "'document', 'lab_order'"
)


def upgrade() -> None:
    op.execute("ALTER TABLE clinical_fact DROP CONSTRAINT clinical_fact_fact_type_check")
    op.execute(
        "ALTER TABLE clinical_fact ADD CONSTRAINT clinical_fact_fact_type_check "
        f"CHECK (fact_type IN ({_TYPES}, 'patient_detail'))"
    )


def downgrade() -> None:
    op.execute("DELETE FROM clinical_fact WHERE fact_type = 'patient_detail'")
    op.execute("ALTER TABLE clinical_fact DROP CONSTRAINT clinical_fact_fact_type_check")
    op.execute(
        "ALTER TABLE clinical_fact ADD CONSTRAINT clinical_fact_fact_type_check "
        f"CHECK (fact_type IN ({_TYPES}))"
    )
