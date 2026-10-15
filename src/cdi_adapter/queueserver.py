"""Which server holds the job queue: Redis or Valkey (backlog SW-S6 FR3, AC2).

Celery talks the same protocol to both, so the swap is the broker address and
``CDI_QUEUE_SERVER``; no code changes. What this module adds is that the worker ASKS the server
what it is when it starts (``INFO server``), and refuses to run when:

* the server is not the one the setting names - a Valkey site that is quietly on Redis, or the
  other way round, is found at start, not in an audit;
* it is Redis 7.4 or newer: from 7.4 Redis is no longer under an open-source licence
  (RSALv2 / SSPLv1; 8.0 adds AGPLv3), so it is not in the "OK" state of the Licence Register
  (story NFR2). Redis 7.2 and older (BSD-3-Clause) and Valkey (BSD-3-Clause) are.

Valkey answers ``server_name:valkey`` and ``valkey_version``; it also says ``redis_version:7.2.4``
for clients that look for it, which is why the name is read first.
"""

from __future__ import annotations

from typing import Any

from cdi_adapter.config import Settings, get_settings
from cdi_adapter.logging import get_logger

log = get_logger(__name__)

LAST_OPEN_REDIS = (7, 2)  # Redis 7.2.x: BSD-3-Clause; 7.4 and later are not open-source


def _version(text: str | None) -> tuple[int, ...]:
    out = []
    for part in str(text or "").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        if not digits:
            break
        out.append(int(digits))
    return tuple(out)


def identify(info: dict[str, Any]) -> dict[str, Any]:
    """What ``INFO server`` says the server is: ``{"server": "valkey"|"redis", "version": ...}``."""
    name = str(info.get("server_name") or "").lower()
    if name == "valkey" or info.get("valkey_version"):
        return {"server": "valkey", "version": str(info.get("valkey_version") or "") or None}
    return {"server": "redis", "version": str(info.get("redis_version") or "") or None}


def problem(info: dict[str, Any], expected: str) -> str | None:
    """Why this server may not hold the queue, or None."""
    found = identify(info)
    if found["server"] != expected:
        return (
            f"CDI_QUEUE_SERVER={expected!r}, but the queue's server is {found['server']} "
            f"{found['version'] or ''}".rstrip()
        )
    if found["server"] == "redis" and _version(found["version"])[:2] > LAST_OPEN_REDIS:
        return (
            f"the queue's server is Redis {found['version']}: Redis after 7.2 is not under an "
            "open-source licence (RSALv2 / SSPLv1), so it is not in the Licence Register's "
            "OK state - use Redis 7.2 or Valkey (CDI_QUEUE_SERVER=valkey)"
        )
    return None


def info_of(url: str) -> dict[str, Any]:  # pragma: no cover - needs a server
    import redis

    client = redis.Redis.from_url(url, socket_connect_timeout=5, socket_timeout=5)
    try:
        found: Any = client.info("server")  # the sync client: a dict, never awaitable
        return dict(found)
    finally:
        client.close()


def check(
    settings: Settings | None = None, *, info: dict[str, Any] | None = None
) -> dict[str, Any]:
    """At a worker's start: what the queue's server is, or ``SystemExit`` when it may not be
    used. A broker that is not a Redis-protocol address (``amqp://``, E9-S8) is not asked."""
    from cdi_adapter.worker import broker_url

    s = settings or get_settings()
    url = broker_url(s)
    if not url.startswith(("redis://", "rediss://", "valkey://", "valkeys://")):
        return {"server": None, "broker": url.split(":", 1)[0]}
    found_info = info if info is not None else info_of(url.replace("valkey", "redis", 1))
    why = problem(found_info, s.queue_server)
    if why:
        raise SystemExit(f"refusing to start: {why}")
    found = identify(found_info)
    log.info("queue_server", **found)
    return found
