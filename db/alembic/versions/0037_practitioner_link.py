"""The doctor list, the link from a document to a doctor, and 'doctor_detail' facts (EX-S2).

* ``practitioner`` - the clinic's doctor list: one row per doctor with the registration number
  the link is made by. Loaded from a file the clinic keeps (``scripts/load_doctor_master.py``).
* ``document_practitioner`` - one row per document: which doctor it was linked to (or none),
  **how** (``registration_number`` / ``name`` / ``unknown``), with what confidence, what was read,
  the doctors it could have been when a link was refused, and whether the page is stamped and
  signed. "unknown" is a row like any other, not a missing one.
* ``clinical_fact.fact_type`` takes ``doctor_detail``: the doctor's name, registration number,
  qualification, department and the clinic's name, address and phone, each as written with its
  proof and status - as ``patient_detail`` does for the patient (0036).

Revision ID: 0037
Revises: 0036
Create Date: 2026-10-12
"""

from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None

_TYPES = (
    "'condition', 'symptom', 'finding', 'lab_result', 'vital_sign', 'procedure', 'medication', "
    "'allergy', 'immunization', 'diagnostic_report', 'advice', 'care_plan', 'referral', "
    "'document', 'lab_order', 'patient_detail'"
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE practitioner (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            name            text NOT NULL,
            registration_no text NOT NULL,
            council         text,
            qualification   text,
            department      text,
            clinic_name     text,
            phone           text,
            active          boolean NOT NULL DEFAULT true,
            source          text,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # one doctor per registration number within a council (two councils may reuse a number)
    op.execute(
        "CREATE UNIQUE INDEX uq_practitioner_registration ON practitioner "
        "(registration_no, coalesce(council, ''))"
    )
    op.execute(
        """
        CREATE TABLE document_practitioner (
            document_id       uuid PRIMARY KEY
                              REFERENCES source_document (id) ON DELETE CASCADE,
            practitioner_id   uuid REFERENCES practitioner (id) ON DELETE SET NULL,
            method            text NOT NULL
                              CHECK (method IN ('registration_number', 'name', 'unknown')),
            confidence        numeric(4, 3),
            registration_read text,
            name_read         text,
            candidates        jsonb NOT NULL DEFAULT '[]',
            why               text,
            stamp_present     boolean,
            signature_present boolean,
            stamp_text        text,
            decided_at        timestamptz NOT NULL DEFAULT now(),
            CHECK (method <> 'unknown' OR practitioner_id IS NULL)
        )
        """
    )
    op.execute("ALTER TABLE clinical_fact DROP CONSTRAINT clinical_fact_fact_type_check")
    op.execute(
        "ALTER TABLE clinical_fact ADD CONSTRAINT clinical_fact_fact_type_check "
        f"CHECK (fact_type IN ({_TYPES}, 'doctor_detail'))"
    )


def downgrade() -> None:
    op.execute("DELETE FROM clinical_fact WHERE fact_type = 'doctor_detail'")
    op.execute("ALTER TABLE clinical_fact DROP CONSTRAINT clinical_fact_fact_type_check")
    op.execute(
        "ALTER TABLE clinical_fact ADD CONSTRAINT clinical_fact_fact_type_check "
        f"CHECK (fact_type IN ({_TYPES}))"
    )
    op.execute("DROP TABLE document_practitioner")
    op.execute("DROP TABLE practitioner")
