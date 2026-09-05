"""Emit empirica diagnostics to cortex's system-events surface (G11).

Shared core for `compliance-report --emit` / `diagnose --emit`: maps a local
result into cortex's POST /v1/system/event envelope and sends it. This is the
diagnostics "freebie" — empirica health rendered in the extension's
System│Diagnostics tab. It is inherently account-gated: /v1/system/event needs
a cortex api_key, so empirica-core-only users can't emit (no funnel leak).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

SYSTEM_EVENT_PATH = "/v1/system/event"
CATEGORY = "diagnostics"

# G11 severity vocabulary: info | notice | warn | critical
_SEVERITY_BY_OVERALL = {"pass": "info", "warn": "warn", "fail": "critical"}


def resolve_cortex_config() -> tuple[str | None, str | None]:
    """(cortex_url, bearer) through the SHARED cortex credential contract.

    Hand-parsed `~/.empirica/credentials.yaml` before this, which was blind
    twice over: to OAuth (an OAuth-only seat has no api_key to find) and to the
    loader's own precedence — a repo-local `.empirica/credentials.yaml`, which
    the loader honours, was invisible to a reader that only ever opened HOME.
    """
    try:
        from empirica.core.auth import cortex_bearer

        creds = cortex_bearer()
        return creds.get("url"), creds.get("bearer")
    except Exception:
        return None, None


def _overall_from_checks(checks: list[dict[str, Any]]) -> tuple[str, int, int]:
    """Derive (overall, passed, counted_total) from a list of check dicts.

    `status` is authoritative ('pass'/'fail'); unavailable/no_data/skipped/warn
    are soft (not counted as failures — a tool absent is a skip, not a fail).
    Any hard fail → overall 'fail'; else any soft → 'warn'; else 'pass'.
    """
    total = passed = 0
    any_fail = any_soft = False
    for c in checks:
        st = (c.get("status") or "").lower()
        if st == "pass":
            total += 1
            passed += 1
        elif st == "fail":
            total += 1
            any_fail = True
        elif st in ("unavailable", "no_data", "skipped", "warn", ""):
            any_soft = True
        elif c.get("passed") is True:  # unknown status, fall back to bool
            total += 1
            passed += 1
        elif c.get("passed") is False:
            total += 1
            any_fail = True
        else:
            any_soft = True
    overall = "fail" if any_fail else ("warn" if any_soft else "pass")
    return overall, passed, total


def compliance_report_to_event(
    report: dict[str, Any],
    *,
    ran_by: str,
    ran_at: str,
    org_id: str | None = None,
    suite: str = "empirica-compliance",
    suite_version: str | None = None,
) -> dict[str, Any]:
    """Map an empirica compliance-report result dict → a /v1/system/event envelope."""
    checks = report.get("checks") or report.get("results") or []
    overall, passed, total = _overall_from_checks(checks)
    score = report.get("score")
    if score is None:  # compliance-report nests it under report["overall"]["score"]
        score = (report.get("overall") or {}).get("score")
    score_str = f" · score {score}" if score is not None else ""
    envelope: dict[str, Any] = {
        "category": CATEGORY,
        "event_type": f"diagnostics_{overall}",
        "severity": _SEVERITY_BY_OVERALL[overall],
        "title": f"{suite} · {ran_by} · {overall}",
        "summary": f"{passed}/{total} checks pass{score_str}",
        "deduplicate_key": f"diagnostics:{ran_by}:{suite}",
        "details": {
            "suite": suite,
            "suite_version": suite_version,
            "ran_by": ran_by,
            "ran_at": ran_at,
            "overall": overall,
            "score": score,
            "checks": checks,
        },
    }
    if org_id:
        envelope["org_id"] = org_id
    return envelope


def emit_system_event(
    envelope: dict[str, Any],
    *,
    cortex_url: str | None = None,
    api_key: str | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict[str, Any]]:
    """POST the envelope to cortex /v1/system/event. Returns (status, body).

    Resolves cortex_url/api_key from credentials.yaml when not supplied. Returns
    (-1, {error}) on no-config or transport failure (never raises).
    """
    if cortex_url is None or api_key is None:
        url2, key2 = resolve_cortex_config()
        cortex_url = cortex_url or url2
        api_key = api_key or key2
    if not (cortex_url and api_key):
        return -1, {"error": "no cortex url/api_key (configure ~/.empirica/credentials.yaml)"}
    url = cortex_url.rstrip("/") + SYSTEM_EVENT_PATH
    req = urllib.request.Request(
        url,
        data=json.dumps(envelope).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"error": str(e)}
    except Exception as e:
        return -1, {"error": f"{type(e).__name__}: {e}"}
