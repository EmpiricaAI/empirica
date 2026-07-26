"""`empirica sources-check` — source link-rot detection (artifact-hygiene WS1).

Probes the http(s) URLs in ``epistemic_sources`` and flags dead / auth-walled /
errored links. **SURFACE-ONLY**: it reports rot, it never deletes — per the
ARTIFACT_HYGIENE.md safety rule, "stale" is a judgment and any deletion goes
through ``delete-artifacts`` (dry-run + receipt) or ``source-archive``, the
operator's call. This is the smallest, safest mechanical slice of the
artifact-hygiene design (spec §7, work-stream 1).

Dependency-injected (``_list_sources`` / ``_probe``) so tests never touch the
network.
"""

from __future__ import annotations

import json
import ssl
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

# Status → category. Redirects resolve to live (the link works, maybe moved);
# 401/403 are auth-walled, not rot; 404/410 are the real dead signal; other
# non-2xx are surfaced as errors for review (could be transient 5xx).
_GATED_CODES = frozenset({401, 403})
_DEAD_CODES = frozenset({404, 410})


def _classify_status(status: int) -> tuple[str, str]:
    if 200 <= status < 400:
        return "live", str(status)
    if status in _GATED_CODES:
        return "gated", str(status)
    if status in _DEAD_CODES:
        return "dead", str(status)
    return "error", str(status)


def _default_probe(url: str, timeout: float = 6.0) -> tuple[str, str]:
    """Probe a URL → (category, detail); category ∈ live|dead|gated|error.

    HEAD first (cheap, no body); on 405/501 (server rejects HEAD) retry GET.
    A connection/DNS/timeout failure is treated as ``dead`` (unreachable).
    Caller-agnostic to redirects — urllib follows them, so a 3xx that lands on
    a 2xx reads as live.
    """
    ctx = ssl.create_default_context()
    headers = {"User-Agent": "empirica-sources-check/1.0"}

    def _attempt(method: str) -> tuple[str, str]:
        req = urllib.request.Request(url, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return _classify_status(resp.status)

    # HEAD first (cheap); if the server rejects HEAD (405/501) fall through to GET.
    try:
        return _attempt("HEAD")
    except urllib.error.HTTPError as e:
        if e.code not in (405, 501):
            return _classify_status(e.code)
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        return "dead", f"{type(e).__name__}: {e}"
    except Exception as e:  # defensive — a probe must not crash the sweep
        return "error", f"{type(e).__name__}: {e}"

    try:
        return _attempt("GET")
    except urllib.error.HTTPError as e:
        return _classify_status(e.code)
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        return "dead", f"{type(e).__name__}: {e}"
    except Exception as e:  # defensive
        return "error", f"{type(e).__name__}: {e}"


def _default_list_sources(project_id: str) -> list[dict]:
    """List this project's epistemic_sources (reuses the canonical lister)."""
    from empirica.cli.command_handlers.artifact_log_commands import _query_epistemic_sources
    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase()
    return _query_epistemic_sources(db, project_id, None, "all", include_archived=False)


def _resolve_project_id(args) -> str | None:
    project_id = getattr(args, "project_id", None)
    if project_id:
        return project_id
    from empirica.utils.session_resolver import InstanceResolver as R

    try:
        project_path = R.project_path()
        if project_path:
            return R.project_id_from_db(project_path)
    except Exception:
        pass
    return None


# Conventional locations a stored source path may resolve against, relative to the
# project root — mirrors the daemon's `_SOURCE_PATH_PREFIXES` so a source the daemon
# can serve is never reported rotted here (and vice versa).
_SOURCE_PATH_PREFIXES = ("", ".empirica/sources", "docs", "docs/sources")


def _looks_like_a_path(value: str) -> bool:
    """Distinguish a locator from a row where `source_url` holds prose.

    Real rows carry titles in this column — e.g. "LarQL - Neural Model as Database"
    - which can never resolve to a file. Calling those "missing" would send a
    gardener hunting for a file that never existed; they need re-pointing, not a
    search. Treat a value as a path only if it has a separator or a short
    file-extension suffix.
    """
    if "/" in value or "\\" in value:
        return True
    suffix = Path(value).suffix
    return bool(suffix) and len(suffix) <= 6 and " " not in suffix


def _classify_local_source(value: str, project_root: Path | None) -> tuple[str, str]:
    """Classify a non-URL source: ``ok`` | ``missing`` | ``not_a_locator``."""
    if not _looks_like_a_path(value):
        return "not_a_locator", "source_url holds a title/label, not a path"
    candidate = Path(value)
    if candidate.is_absolute():
        if candidate.is_file():
            return "ok", str(candidate)
        return "missing", f"absolute path not on disk: {candidate}"
    tried = 0
    if project_root:
        for prefix in _SOURCE_PATH_PREFIXES:
            resolved = (project_root / prefix / candidate) if prefix else (project_root / candidate)
            tried += 1
            if resolved.is_file():
                return "ok", str(resolved)
    return "missing", f"not found under project root (tried {tried} location(s))"


def _is_probeable(url) -> bool:
    return isinstance(url, str) and url.startswith(("http://", "https://"))


def _parse_discovered_at(value) -> float | None:
    """Best-effort parse of a source's discovered_at into a unix timestamp."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip()
        try:
            return float(s)  # already a unix-ts string
        except ValueError:
            pass
        try:
            from datetime import datetime

            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except (ValueError, OSError):
            return None
    return None


def _should_probe(discovered_at, staleness_days: int, now: float) -> bool:
    """True if a source is due for a re-probe.

    ``staleness_days <= 0`` probes everything. Otherwise a source is probed once
    it's older than the threshold; fresh sources (just added, presumed live) are
    skipped to keep the sweep cheap. An unparseable/absent ``discovered_at``
    probes anyway — never skip on missing data.
    """
    if staleness_days <= 0:
        return True
    ts = _parse_discovered_at(discovered_at)
    if ts is None:
        return True
    return (now - ts) >= staleness_days * 86400


def _format_human(result: dict) -> str:
    lines = [
        f"🔗 sources-check — {result['checked']} URL(s) probed "
        f"({result['live']} live, {len(result['dead'])} dead, "
        f"{len(result['gated'])} gated, {len(result['errored'])} errored; "
        f"{result['skipped_no_url']} non-URL + {result.get('skipped_fresh', 0)} "
        f"fresh(<{result.get('staleness_days', 0)}d) skipped)",
    ]
    for tag, rows in (("DEAD", result["dead"]), ("GATED", result["gated"]), ("ERROR", result["errored"])):
        for r in rows:
            lines.append(f"  [{tag}] {r['status']:<20} {r.get('title') or '?'} — {r['url']}")
    if not (result["dead"] or result["gated"] or result["errored"]):
        lines.append("  ✅ all probed source links resolve")
    else:
        lines.append(
            "  (surface-only — dead links stay logged. To retire one: "
            "`empirica delete-artifacts` (dry-run+receipt) or `source-archive`.)"
        )

    local_missing = result.get("local_missing") or []
    not_a_locator = result.get("not_a_locator") or []
    if result.get("local_ok") or local_missing or not_a_locator:
        lines.append(f"  Local files OK:   {result.get('local_ok', 0)}")
        if local_missing:
            lines.append(f"  MISSING on disk:  {len(local_missing)}")
            for r in local_missing[:5]:
                lines.append(f"    - {str(r.get('title'))[:44]} — {r.get('status')}")
        if not_a_locator:
            lines.append(f"  Not a locator:    {len(not_a_locator)}  (source_url holds a title — re-point these)")
            for r in not_a_locator[:5]:
                lines.append(f"    - {str(r.get('title'))[:44]}")

    return "\n".join(lines)


def handle_sources_check_command(
    args,
    *,
    _list_sources: Callable[[str], list[dict]] = _default_list_sources,
    _probe: Callable[[str, float], tuple[str, str]] = _default_probe,
) -> int:
    """`empirica sources-check` — probe source URLs, surface link-rot.

    Exit 1 iff any source URL is confirmed DEAD (404/410/unreachable) — usable
    as a CI/hygiene gate. gated/errored do NOT fail (auth-walled or transient).
    """
    output_format = getattr(args, "output", "human")
    timeout = float(getattr(args, "timeout", 6.0))

    project_id = _resolve_project_id(args)
    if not project_id:
        sys.stderr.write("sources-check: could not resolve project_id — pass --project-id.\n")
        return 1

    try:
        sources = _list_sources(project_id)
    except Exception as e:
        sys.stderr.write(f"sources-check: failed to list sources: {type(e).__name__}: {e}\n")
        return 1

    has_url = [s for s in sources if _is_probeable(s.get("url") or s.get("source_url"))]
    skipped = len(sources) - len(has_url)

    # Per-practice staleness (hygiene_policy WS2): only re-probe sources older
    # than the threshold — fresh ones are presumed live. --staleness-days
    # overrides the policy; 0 probes everything.
    staleness_days = getattr(args, "staleness_days", None)
    if staleness_days is None:
        from empirica.config.hygiene_policy import resolve_hygiene_policy

        staleness_days = int(resolve_hygiene_policy().get("source_staleness_days", 30))
    now = time.time()
    probeable = [s for s in has_url if _should_probe(s.get("discovered_at"), staleness_days, now)]
    fresh_skipped = len(has_url) - len(probeable)

    live = 0
    dead: list[dict] = []
    gated: list[dict] = []
    errored: list[dict] = []
    for s in probeable:
        url = s.get("url") or s.get("source_url")
        category, detail = _probe(url, timeout)
        rec = {"id": s.get("id"), "title": s.get("title"), "url": url, "status": detail}
        if category == "live":
            live += 1
        elif category == "gated":
            gated.append(rec)
        elif category == "dead":
            dead.append(rec)
        else:
            errored.append(rec)

    # Local (non-URL) sources: the URL probe skips these entirely, so file rot was
    # invisible — measured 25 of 50 unservable on one practice while sources-check
    # reported clean. A source whose file is gone is exactly as dead as a 404.
    project_root = None
    try:
        from empirica.utils.session_resolver import InstanceResolver as _R

        _pp = _R.project_path()
        project_root = Path(_pp) if _pp else None
    except Exception:
        project_root = None

    local_ok = 0
    local_missing: list[dict] = []
    not_a_locator: list[dict] = []
    for s in sources:
        value = s.get("url") or s.get("source_url")
        if not value or _is_probeable(value):
            continue
        category, detail = _classify_local_source(str(value), project_root)
        rec = {"id": s.get("id"), "title": s.get("title"), "url": value, "status": detail}
        if category == "ok":
            local_ok += 1
        elif category == "missing":
            local_missing.append(rec)
        else:
            not_a_locator.append(rec)

    result = {
        "ok": True,
        "project_id": project_id,
        "checked": len(probeable),
        "local_ok": local_ok,
        "local_missing": local_missing,
        "not_a_locator": not_a_locator,
        "live": live,
        "dead": dead,
        "gated": gated,
        "errored": errored,
        "skipped_no_url": skipped,
        "skipped_fresh": fresh_skipped,
        "staleness_days": staleness_days,
    }

    if output_format == "json":
        sys.stdout.write(json.dumps(result, indent=2) + "\n")
    else:
        sys.stdout.write(_format_human(result) + "\n")

    return 1 if (dead or local_missing) else 0
