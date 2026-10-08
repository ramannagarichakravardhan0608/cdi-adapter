"""The weekly picture-quality report: how often a picture had to be retaken, and why
(backlog UP-S2 AC5).

    python -m cdi_adapter.webapp.quality_report            # the last 7 days
    python -m cdi_adapter.webapp.quality_report --days 30 --json
    GET /api/reports/quality?days=7                        # the same numbers, as JSON

What it answers, for the last ``days`` days:

* **retake rate** - of all the pictures sent, the share that were held with "please take it
  again" (``held / pictures_sent``);
* **top reasons** - why they were held, most common first (too small, blurred, too dark ...);
* **retaken** - how many held pages the person did replace, so a reason people give up on shows.

Where the numbers come from. A held page is never ingested - that is the point of holding it - so
it has no document row to count. And when it is retaken, its place in the job is overwritten by
the new picture, so the job no longer says it was ever held. So each hold is written to the
append-only ``audit_log`` the moment it happens (:func:`record_hold`, called by the job runner),
beside the row a retake already writes. The report reads those two kinds of row and the number of
pages in ``web_job``. Counts only: no file name, no patient, no measurement leaves this module.

**Holds are counted from the day this was deployed.** A page held before that left no audit row
and is not in the report. And with ``CDI_QUALITY_CHECK`` off nothing is held, so the rate is 0.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from cdi_adapter import repo
from cdi_adapter.logging import get_logger

log = get_logger(__name__)

ENTITY = "web_job"
HOLD = "quality_hold"  # audit_log.detail key written by record_hold
RETAKE = "replaced_page"  # audit_log.detail key written by jobstore.replace_page


def record_hold(conn: Connection, *, job_id: str, page: int, code: str, actor: str) -> None:
    """One page was held: remember that it was, and why. ``page`` counts from 1."""
    repo.write_audit(conn, actor=actor, action="update", entity=ENTITY, entity_id=job_id,
                     detail={HOLD: {"page": page, "code": code}})  # fmt: skip


def weekly(conn: Connection, *, days: int = 7, now: datetime | None = None) -> dict[str, Any]:
    """The report for the ``days`` days up to ``now``."""
    until = now or datetime.now(tz=UTC)
    since = until - timedelta(days=days)
    span = {"since": since, "until": until}
    pages, uploads = conn.execute(
        text("SELECT coalesce(sum(jsonb_array_length(docs)), 0), count(*) FROM web_job "
             "WHERE created_at >= :since AND created_at < :until"), span).one()  # fmt: skip
    reasons = conn.execute(
        text("SELECT detail #>> '{quality_hold,code}' AS code, count(*) AS held FROM audit_log "
             "WHERE entity = 'web_job' AND detail ? 'quality_hold' "
             "  AND at >= :since AND at < :until "
             "GROUP BY 1 ORDER BY held DESC, code"), span).all()  # fmt: skip
    retaken = conn.execute(
        text("SELECT count(*) FROM audit_log WHERE entity = 'web_job' "
             "  AND detail ? 'replaced_page' AND at >= :since AND at < :until"),
        span).scalar_one()  # fmt: skip
    return summarise(int(pages), int(uploads), [(str(c), int(n)) for c, n in reasons],
                     int(retaken), since=since, until=until, days=days)  # fmt: skip


def summarise(pages: int, uploads: int, reasons: list[tuple[str, int]], retaken: int, *,
              since: datetime, until: datetime, days: int) -> dict[str, Any]:  # fmt: skip
    """The arithmetic, apart from the database so it can be checked on its own.

    ``pictures_sent`` is every picture a person sent: the pages of every upload, plus every
    retake (a retake takes a page's place in its upload, so it is not in the page count)."""
    held = sum(n for _, n in reasons)
    sent = pages + retaken
    top_reasons = [{"code": code, "reason": code.replace("_", " "), "held": n,
                    "share_of_held": round(n / held, 4)}
                   for code, n in sorted(reasons, key=lambda r: (-r[1], r[0]))]  # fmt: skip
    return {
        "period": {"days": days, "since": since.isoformat(), "until": until.isoformat()},
        "uploads": uploads,
        "pictures_sent": sent,
        "held": held,
        "retake_rate": round(held / sent, 4) if sent else None,
        "retaken": retaken,
        "retaken_share_of_held": round(retaken / held, 4) if held else None,
        "top_reasons": top_reasons,
    }


def as_text(report: dict[str, Any]) -> str:
    """The report as a person reads it."""
    p = report["period"]
    rate = report["retake_rate"]
    lines = [
        f"Picture quality, last {p['days']} day(s)   {p['since'][:10]} to {p['until'][:10]}",
        "",
        f"  uploads            {report['uploads']}",
        f"  pictures sent      {report['pictures_sent']}",
        f"  held for a retake  {report['held']}",
        f"  retake rate        {'-' if rate is None else f'{100 * rate:.1f} %'}",
        f"  retaken            {report['retaken']}",
        "",
        "  Top reasons",
    ]
    if not report["top_reasons"]:
        lines.append("    (no picture was held)")
    for r in report["top_reasons"]:
        lines.append(f"    {r['reason']:<18}{r['held']:>6}   {100 * r['share_of_held']:5.1f} %")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="quality_report", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=7, help="how many days back (default 7)")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    args = ap.parse_args(argv)
    if not 1 <= args.days <= 366:
        ap.error("--days must be between 1 and 366")
    from cdi_adapter.db import session_scope

    with session_scope() as conn:
        report = weekly(conn, days=args.days)
    print(json.dumps(report, indent=2) if args.json else as_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
