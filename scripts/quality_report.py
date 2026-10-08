"""The weekly picture-quality report (backlog UP-S2 AC5): retake rate and top reasons.

    python scripts/quality_report.py            # the last 7 days
    python scripts/quality_report.py --days 30
    python scripts/quality_report.py --json

Reads the database the settings name; writes nothing. The report itself, and what it counts, is
``cdi_adapter/webapp/quality_report.py``.
"""

from cdi_adapter.webapp.quality_report import main

if __name__ == "__main__":
    raise SystemExit(main())
