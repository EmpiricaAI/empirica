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
import urllib.error
import urllib.request
from collections.abc import Callable

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


def _is_probeable(url) -> bool:
    return isinstance(url, str) and url.startswith(("http://", "https://"))


def _format_human(result: dict) -> str:
    lines = [
        f"🔗 sources-check — {result['checked']} URL(s) probed "
        f"({result['live']} live, {len(result['dead'])} dead, "
        f"{len(result['gated'])} gated, {len(result['errored'])} errored; "
        f"{result['skipped_no_url']} non-URL sources skipped)",
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

    probeable = [s for s in sources if _is_probeable(s.get("url") or s.get("source_url"))]
    skipped = len(sources) - len(probeable)

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

    result = {
        "ok": True,
        "project_id": project_id,
        "checked": len(probeable),
        "live": live,
        "dead": dead,
        "gated": gated,
        "errored": errored,
        "skipped_no_url": skipped,
    }

    if output_format == "json":
        sys.stdout.write(json.dumps(result, indent=2) + "\n")
    else:
        sys.stdout.write(_format_human(result) + "\n")

    return 1 if dead else 0
