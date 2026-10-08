"""UP-S2 AC5 on real PostgreSQL: the weekly picture-quality report reads what was written.

The arithmetic is unit-tested (tests/test_up_s2_acceptance_unit.py). This proves the three
queries against the real tables: holds and retakes from the append-only ``audit_log``, pages from
``web_job``, all inside the window asked for.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from cdi_adapter import repo
from cdi_adapter.db import session_scope
from cdi_adapter.webapp import quality_report

pytestmark = pytest.mark.integration


def _job(pages: int) -> str:
    job_id = uuid.uuid4().hex
    docs = [{"filename": f"p{n}.png", "status": "done"} for n in range(pages)]
    with session_scope() as c:
        c.execute(text("INSERT INTO web_job (id, files, docs, created_by) "
                       "VALUES (:i, '[]'::jsonb, CAST(:d AS jsonb), 'desk')"),
                  {"i": job_id, "d": json.dumps(docs)})  # fmt: skip
    return job_id


def test_the_report_counts_holds_reasons_and_retakes_written_this_week(db_env):
    with session_scope() as c:
        before = quality_report.weekly(c)
    job = _job(pages=3)
    with session_scope() as c:
        for page, code in ((1, "too_small"), (2, "too_small"), (3, "blurred")):
            quality_report.record_hold(c, job_id=job, page=page, code=code, actor="runner")
        repo.write_audit(
            c,
            actor="desk",
            action="update",
            entity="web_job",
            entity_id=job,
            detail={"replaced_page": 1, "filename": "again.png", "bytes": 10},
        )
    with session_scope() as c:
        after = quality_report.weekly(c)
    assert after["held"] - before["held"] == 3
    assert after["retaken"] - before["retaken"] == 1
    assert after["pictures_sent"] - before["pictures_sent"] == 4  # three pages and one retake
    counts = {r["code"]: r["held"] for r in after["top_reasons"]}
    was = {r["code"]: r["held"] for r in before["top_reasons"]}
    assert counts["too_small"] - was.get("too_small", 0) == 2
    assert counts["blurred"] - was.get("blurred", 0) == 1
    held = [r["held"] for r in after["top_reasons"]]
    assert held == sorted(held, reverse=True)  # most common first
    assert 0 < after["retake_rate"] <= 1


def test_what_happened_before_the_window_is_not_counted(db_env):
    job = _job(pages=1)
    with session_scope() as c:
        quality_report.record_hold(c, job_id=job, page=1, code="too_dark", actor="runner")
    long_ago = datetime.now(tz=UTC) - timedelta(days=400)
    with session_scope() as c:
        then = quality_report.weekly(c, now=long_ago)
    assert then["held"] == 0 and then["retaken"] == 0 and then["retake_rate"] is None
