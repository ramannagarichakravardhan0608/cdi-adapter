"""The alerts that are firing now, for the banner every page shows (LS-S9 AC4, LS-S7 AC4).

The clinic has named no email, chat or pager for the ``oncall`` receiver, so the staff who are
signed in are the people an alert can reach: the web app asks Alertmanager (loopback only,
``CDI_ALERTMANAGER_URL``) what is firing and each page shows it in a banner. Alertmanager stays
the one place that decides what is firing - an alert that is silenced or inhibited there is not
shown - and nothing can be posted to the web app, so the banner cannot be forged from outside.

Only an alert's name, severity, summary and start time are passed on: the rules' summaries are
fixed sentences with no patient content (infra/monitoring/rules/cdi-alerts.yml).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import httpx

from cdi_adapter.config import get_settings
from cdi_adapter.logging import get_logger

log = get_logger(__name__)

SEVERITIES = ("page", "ticket")  # the two the rules use; "page" is shown first, in red
CACHE_SECONDS = 15.0  # the rules are evaluated every 15 s: asking more often shows nothing new
QUERY = "/api/v2/alerts?active=true&silenced=false&inhibited=false"

Fetch = Callable[[str], list[dict[str, Any]]]

_lock = threading.Lock()
_cached: tuple[float, str, dict[str, Any]] | None = None


def _fetch(url: str) -> list[dict[str, Any]]:
    response = httpx.get(url, timeout=2.0)
    response.raise_for_status()
    found: list[dict[str, Any]] = response.json()
    return found


def shown(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What the banner shows of Alertmanager's answer: one line per alert name, pages first."""
    seen: dict[str, dict[str, Any]] = {}
    for alert in raw:
        labels, notes = alert.get("labels") or {}, alert.get("annotations") or {}
        name, severity = str(labels.get("alertname") or ""), str(labels.get("severity") or "")
        if not name or severity not in SEVERITIES:
            continue
        if (alert.get("status") or {}).get("state", "active") != "active":
            continue
        since = str(alert.get("startsAt") or "")
        if name in seen and seen[name]["since"] <= since:
            continue  # the same alert for several series: keep the earliest start
        seen[name] = {"name": name, "severity": severity, "since": since,
                      "summary": str(notes.get("summary") or name)}  # fmt: skip
    return sorted(seen.values(), key=lambda a: (SEVERITIES.index(a["severity"]), a["since"]))


def active(url: str | None = None, *, fetch: Fetch = _fetch,
           clock: Callable[[], float] = time.monotonic) -> dict[str, Any]:  # fmt: skip
    """``{"reachable": bool, "alerts": [...]}``. Never raises: with no Alertmanager set or none
    answering, ``reachable`` is false and the banner stays empty - a page must still open."""
    global _cached
    base = (get_settings().alertmanager_url if url is None else url).rstrip("/")
    if not base:
        return {"reachable": False, "alerts": []}
    with _lock:
        if _cached and _cached[1] == base and clock() - _cached[0] < CACHE_SECONDS:
            return _cached[2]
    try:
        answer = {"reachable": True, "alerts": shown(fetch(base + QUERY))}
    except Exception as exc:
        log.warning("alerts_unreachable", error=type(exc).__name__)
        answer = {"reachable": False, "alerts": []}
    with _lock:
        _cached = (clock(), base, answer)
    return answer


def forget() -> None:
    """Drop the cached answer (tests)."""
    global _cached
    with _lock:
        _cached = None
