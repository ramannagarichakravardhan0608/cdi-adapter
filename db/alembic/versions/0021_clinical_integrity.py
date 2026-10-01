"""Preserve birth-date precision and canonical as-needed dosing.

Revision ID: 0021
Revises: 0020
"""

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE patient_identity ADD COLUMN birth_date_precision text "
        "DEFAULT 'day' CHECK (birth_date_precision IN ('day', 'year'))"
    )
    # Historical January-1 estimates must not become asserted full birth dates. Only registry
    # or full-date document evidence establishes that precision for existing identities.
    op.execute(
        "UPDATE patient_identity p SET birth_date_precision = CASE WHEN "
        "EXISTS (SELECT 1 FROM patient_registry r WHERE r.patient_id = p.mpi_id "
        "AND r.dob = p.birth_date) OR EXISTS (SELECT 1 FROM patient_identity_alias a "
        "WHERE a.patient_id = p.id AND a.birth_date = p.birth_date) "
        "THEN 'day' ELSE 'year' END WHERE p.birth_date IS NOT NULL"
    )
    op.execute("ALTER TABLE cn_medication ADD COLUMN prn boolean NOT NULL DEFAULT false")
    op.execute(
        "UPDATE cn_medication c SET prn = m.prn FROM medication_detail m "
        "WHERE m.fact_id = c.source_fact_id"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE cn_medication DROP COLUMN prn")
    op.execute("ALTER TABLE patient_identity DROP COLUMN birth_date_precision")
