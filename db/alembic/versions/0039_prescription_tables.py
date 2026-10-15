"""The prescription in clear tables: lab tests, preparation, test context; views for the
patient's and the doctor's details (backlog OUT-S1).

Nothing existing is altered. Three new tables, each row pointing at the ``clinical_fact`` it
describes - and through it at its evidence - and removed with it (ON DELETE CASCADE), so a row
never outlives its fact and a document read again leaves no old rows behind:

* ``rx_lab_test``     - an ordered test: as written, standard name and code, kind, specimen,
                        urgency, how it was matched, the "does not fit" check;
* ``rx_preparation``  - a preparation note: original words, type, amount, unit, what it applies
                        to and the tests it is linked to;
* ``rx_test_context`` - a test and a reason written on the same prescription, and how the page
                        connects them. One row per pair.

They are written by ``cdi_adapter.persist.normalized.refresh`` in the same transaction as the
facts. The views give every kind of value **with its evidence** (page, box, the OCR lines):

* ``v_rx_evidence``        - every current value of a document with its proof;
* ``v_rx_patient_detail``  - the patient's details read from the page (EX-S1);
* ``v_rx_doctor_detail``   - the doctor's details, with how the doctor was identified (EX-S2);
* ``v_rx_lab_test``, ``v_rx_preparation``, ``v_rx_test_context`` - the tables above, with proof.

Revision ID: 0039
Revises: 0038
Create Date: 2026-10-13
"""

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None

VIEWS = ("v_rx_test_context", "v_rx_preparation", "v_rx_lab_test", "v_rx_doctor_detail",
         "v_rx_patient_detail", "v_rx_evidence")  # fmt: skip
TABLES = ("rx_test_context", "rx_preparation", "rx_lab_test")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE rx_lab_test (
            fact_id        uuid PRIMARY KEY REFERENCES clinical_fact (id) ON DELETE CASCADE,
            document_id    uuid NOT NULL REFERENCES source_document (id) ON DELETE CASCADE,
            as_written     text NOT NULL,
            standard_name  text,
            code_system    text,
            code           text,
            kind           text CHECK (kind IN ('test', 'panel', 'study')),
            specimen       text,
            urgency        text,
            match          text NOT NULL CHECK (match IN ('matched', 'doubtful', 'not_matched')),
            does_not_fit   boolean NOT NULL DEFAULT false,
            master_version text NOT NULL,
            written_at     timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_rx_lab_test_document ON rx_lab_test (document_id)")
    op.execute(
        """
        CREATE TABLE rx_preparation (
            fact_id         uuid PRIMARY KEY REFERENCES clinical_fact (id) ON DELETE CASCADE,
            document_id     uuid NOT NULL REFERENCES source_document (id) ON DELETE CASCADE,
            text            text NOT NULL,
            type            text NOT NULL CHECK (type IN ('fasting', 'timing', 'diet',
                                'hold_medicine', 'collection', 'bring_documents', 'other')),
            amount          numeric,
            unit            text,
            range_low       numeric,
            range_high      numeric,
            applies_to      text NOT NULL CHECK (applies_to IN ('all', 'unclear', 'tests')),
            test_fact_ids   uuid[] NOT NULL DEFAULT '{}',
            phrases_version text NOT NULL,
            written_at      timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_rx_preparation_document ON rx_preparation (document_id)")
    op.execute(
        """
        CREATE TABLE rx_test_context (
            id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            document_id    uuid NOT NULL REFERENCES source_document (id) ON DELETE CASCADE,
            test_fact_id   uuid NOT NULL REFERENCES clinical_fact (id) ON DELETE CASCADE,
            reason_fact_id uuid NOT NULL REFERENCES clinical_fact (id) ON DELETE CASCADE,
            reason_type    text NOT NULL CHECK (reason_type IN ('diagnosis', 'complaint', 'note')),
            reason_text    text NOT NULL,
            relation       text NOT NULL
                           CHECK (relation IN ('same_line', 'same_page', 'other_page')),
            fits           boolean,
            rules_version  text NOT NULL,
            written_at     timestamptz NOT NULL DEFAULT now(),
            UNIQUE (test_fact_id, reason_fact_id)
        )
        """
    )
    op.execute("CREATE INDEX ix_rx_test_context_document ON rx_test_context (document_id)")
    op.execute(
        """
        CREATE VIEW v_rx_evidence AS
        SELECT f.id AS fact_id, s.document_id, f.patient_id, f.fact_type, f.local_text,
               f.value_text, f.value_num, f.value_unit_ucum, f.code_system, f.code,
               f.code_display, f.review_state, f.confidence_overall, f.extraction_id,
               p.id AS evidence_id, p.page_id, g.page_no, p.bbox_union, p.ocr_block_ids,
               p.extracted_text AS evidence_text, p.model_stack
        FROM clinical_fact f
        CROSS JOIN LATERAL unnest(f.source_doc_ids) AS s(document_id)
        LEFT JOIN LATERAL (SELECT q.* FROM fact_provenance q WHERE q.fact_id = f.id
                           ORDER BY q.recorded_at, q.id LIMIT 1) p ON true
        LEFT JOIN document_page g ON g.id = p.page_id
        WHERE f.is_current AND f.review_state <> 'rejected'
        """
    )
    op.execute(
        """
        CREATE VIEW v_rx_patient_detail AS
        SELECT e.document_id, e.patient_id, e.fact_id, e.local_text AS detail,
               e.value_text AS value, e.review_state, e.confidence_overall, e.evidence_id,
               e.page_no, e.bbox_union, e.ocr_block_ids, e.evidence_text, e.model_stack
        FROM v_rx_evidence e WHERE e.fact_type = 'patient_detail'
        """
    )
    op.execute(
        """
        CREATE VIEW v_rx_doctor_detail AS
        SELECT e.document_id, e.fact_id, e.local_text AS detail, e.value_text AS value,
               e.review_state, e.confidence_overall, e.evidence_id, e.page_no, e.bbox_union,
               e.ocr_block_ids, e.evidence_text, e.model_stack,
               l.method AS link_method, l.confidence AS link_confidence, l.practitioner_id,
               l.stamp_present, l.signature_present
        FROM v_rx_evidence e
        LEFT JOIN document_practitioner l ON l.document_id = e.document_id
        WHERE e.fact_type = 'doctor_detail'
        """
    )
    op.execute(
        """
        CREATE VIEW v_rx_lab_test AS
        SELECT t.*, e.review_state, e.confidence_overall, e.evidence_id, e.page_no,
               e.bbox_union, e.ocr_block_ids, e.evidence_text, e.model_stack
        FROM rx_lab_test t
        JOIN v_rx_evidence e ON e.fact_id = t.fact_id AND e.document_id = t.document_id
        """
    )
    op.execute(
        """
        CREATE VIEW v_rx_preparation AS
        SELECT r.*, e.review_state, e.confidence_overall, e.evidence_id, e.page_no,
               e.bbox_union, e.ocr_block_ids, e.evidence_text, e.model_stack
        FROM rx_preparation r
        JOIN v_rx_evidence e ON e.fact_id = r.fact_id AND e.document_id = r.document_id
        """
    )
    op.execute(
        """
        CREATE VIEW v_rx_test_context AS
        SELECT c.id, c.document_id, c.test_fact_id, t.as_written AS test_as_written,
               c.reason_fact_id, c.reason_type, c.reason_text, c.relation, c.fits,
               c.rules_version, e.evidence_id, e.page_no, e.bbox_union, e.ocr_block_ids,
               e.evidence_text
        FROM rx_test_context c
        JOIN rx_lab_test t ON t.fact_id = c.test_fact_id
        JOIN v_rx_evidence e ON e.fact_id = c.reason_fact_id AND e.document_id = c.document_id
        """
    )


def downgrade() -> None:
    for view in VIEWS:
        op.execute(f"DROP VIEW IF EXISTS {view}")
    for table in TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")
