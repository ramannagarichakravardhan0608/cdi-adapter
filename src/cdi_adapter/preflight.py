"""Read-only production configuration and dependency checks.

Run ``python -m cdi_adapter.preflight`` with the deployed environment before routing traffic.
No credentials or exception bodies are printed. Clinical release approval is a separate gate.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from cdi_adapter.config import Settings


def configuration_issues(s: Settings) -> list[str]:
    from cdi_adapter import secretstore
    from cdi_adapter.training.thresholds import check_config

    issues = []
    if s.mlserve_backend == "stub":
        issues.append("CDI_MLSERVE_BACKEND must select a real model")
    if not s.vlm_model_revision or not re.fullmatch(r"[0-9a-f]{40}", s.vlm_model_revision):
        issues.append("CDI_VLM_MODEL_REVISION must pin a full commit")
    if not s.model_version:
        issues.append("CDI_MODEL_VERSION is required")
    if not s.session_cookie_secure:
        issues.append("CDI_SESSION_COOKIE_SECURE must be true")
    if s.job_execution != "queue":
        issues.append("CDI_JOB_EXECUTION must be queue")
    if not s.fhir_require_hapi:
        issues.append("CDI_FHIR_REQUIRE_HAPI must be true")
    for field in secretstore.FIELDS:
        if secretstore._secret_strength(f"cdi_{field}", getattr(s, field)):
            issues.append(f"CDI_{field.upper()} must use a non-default strong credential")
    if not s.calibrator_version or not s.gate_thresholds:
        issues.append("A fitted calibrator and matching gate thresholds are required")
    else:
        try:
            check_config(s.gate_thresholds, s.calibrator_version)
        except Exception:
            issues.append("Gate thresholds are unreadable, invalid or use another calibrator")
    if not Path(s.fhir_validator_jar).is_file():
        issues.append("The pinned HL7 validator jar is missing")
    return issues


def checks() -> dict[str, Any]:
    from sqlalchemy import text

    from cdi_adapter.config import get_settings
    from cdi_adapter.db import session_scope
    from cdi_adapter.fhir.validate_abdm import hapi_available
    from cdi_adapter.ml.client import HttpMLClient
    from cdi_adapter.storage import get_store

    s = get_settings()
    result: dict[str, Any] = {"configuration": configuration_issues(s)}
    result["production_mode"] = s.production
    result["hl7_validator"] = hapi_available()
    try:
        with session_scope() as c:
            result["schema"] = (
                c.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0021"
            )
            result["calibrator"] = bool(
                c.execute(
                    text("SELECT 1 FROM calibration_model WHERE version=:v"),
                    {"v": s.calibrator_version},
                ).first()
            )
    except Exception:
        result["schema"] = result["calibrator"] = False
    try:
        result["object_store"] = get_store().healthy()
    except Exception:
        result["object_store"] = False
    try:
        import redis

        result["queue"] = bool(
            redis.Redis.from_url(
                s.redis_url,
                socket_connect_timeout=3,
                socket_timeout=3,
            ).ping()
        )
    except Exception:
        result["queue"] = False
    try:
        health = HttpMLClient(timeout=3).healthz()
        result["model"] = (
            health.get("status") == "ok"
            and health.get("version") == s.model_version
            and bool(s.model_version)
            and health.get("backend") == s.mlserve_backend
            and bool(health.get("loaded"))
        )
    except Exception:
        result["model"] = False
    return result


def main() -> int:
    try:
        result = checks()
    except Exception as exc:
        # Settings errors may contain URLs: never print their bodies.
        print(json.dumps({"ready": False, "configuration_error": type(exc).__name__}))
        return 1
    ok = not result["configuration"] and all(v for k, v in result.items() if k != "configuration")
    print(json.dumps({"ready": ok, "checks": result}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
