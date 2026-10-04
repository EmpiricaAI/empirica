"""The calibration bias block that session hooks put in front of the AI.

`.breadcrumbs.yaml` carries what the post-test verification measured about how this practitioner's
self-assessments differ from the evidence. The block is how that reaches the next window. It was
written once against a `calibration:` key the file no longer has, so after the writer moved to
`grounded_calibration:` the post-compact loader returned nothing, silently, and session start never
had a reader (2026-10-02, found while checking the paper's account of what PREFLIGHT injects).

One formatter, called by both hooks. It states what the numbers are and are not, how many
observations stand behind them and how old they are, because an unlabelled bias profile reads as a
verdict and a stale one reads as current.

Per-task calibration warnings at PREFLIGHT stay withheld (they would be an answer key for the next
self-assessment). This is the cross-transaction profile, and the system prompt tells the practitioner
to internalize it.
"""

from __future__ import annotations

import math
import re
import time
from datetime import datetime
from pathlib import Path

#: A vector is worth naming only when the evidence disagrees with the self-assessment by this much.
MIN_DIVERGENCE = 0.10
MAX_VECTORS = 6
MAX_INSIGHTS = 3
#: Past this age the profile is shown but labelled stale.
STALE_AFTER_DAYS = 7


#: Insight text is written by code but injected into the AI's context, and `.breadcrumbs.yaml` can come
#: from a cloned repo. One short line each: no newline can start a fake heading, and none is unbounded.
MAX_TEXT = 200
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_ -]{0,40}")


def _one_line(value: object) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + "…"


def _severity(value: object) -> float:
    try:
        sev = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return sev if math.isfinite(sev) else 0.0


def _age_days(stamp: object, now: float) -> float | None:
    try:
        return (now - datetime.fromisoformat(str(stamp)).timestamp()) / 86400
    except (TypeError, ValueError):
        return None


def format_calibration_block(config: dict, now: float | None = None) -> str:
    """The block for a loaded `.breadcrumbs.yaml`, or "" when it holds no grounded calibration."""
    cal = config.get("grounded_calibration") if isinstance(config, dict) else None
    if not isinstance(cal, dict):
        return ""
    div = cal.get("divergence")
    divergence = (
        {
            str(k): float(v)
            for k, v in div.items()
            if isinstance(v, (int, float))
            and not isinstance(v, bool)
            and math.isfinite(v)
            and _NAME_RE.fullmatch(str(k))
        }
        if isinstance(div, dict)
        else {}
    )
    if not divergence:
        return ""

    now = time.time() if now is None else now
    age = _age_days(cal.get("last_updated"), now)
    when = str(cal["last_updated"])[:10] if cal.get("last_updated") else "unknown date"
    stale = f", STALE ({age:.0f} days old)" if age is not None and age > STALE_AFTER_DAYS else ""
    n = cal.get("observations")
    counted = f", {n:,} observations" if isinstance(n, int) and not isinstance(n, bool) else ""
    header = f"### Calibration (grounded{counted}, updated {when}{stale})"

    named = sorted((kv for kv in divergence.items() if abs(kv[1]) >= MIN_DIVERGENCE), key=lambda kv: -abs(kv[1]))
    lines = [
        header,
        "Drift indicators from deterministic proxies, not ground truth: read them beside your own sense of the work.",
        "How your self-assessments have differed from the evidence (+ = you read higher than the evidence, - = lower):",
    ]
    if named:
        lines.append("  " + " · ".join(f"{k} {v:+.2f}" for k, v in named[:MAX_VECTORS]))
    else:
        lines.append(f"  no vector differs by {MIN_DIVERGENCE:.2f} or more")

    insights = [i for i in (cal.get("insights") or []) if isinstance(i, dict) and i.get("description")]
    insights.sort(key=lambda i: -_severity(i.get("severity")))
    for i in insights[:MAX_INSIGHTS]:
        lines.append(f"  - {_one_line(i['description'])}")

    excluded = cal.get("excluded")
    for item in (excluded if isinstance(excluded, list) else [])[:3]:
        if not isinstance(item, dict):
            continue
        names = [str(v) for v in (item.get("vectors") or []) if _NAME_RE.fullmatch(str(v))]
        count = item.get("observations")
        if names and isinstance(count, int) and not isinstance(count, bool):
            lines.append(
                f"Excluded as known-bad measurements ({', '.join(names)}, {count:,} observations): "
                f"{_one_line(item.get('reason') or 'no reason recorded')}"
            )

    ungrounded = cal.get("ungrounded")
    names = [str(u) for u in ungrounded if _NAME_RE.fullmatch(str(u))] if isinstance(ungrounded, list) else []
    if names:
        lines.append(f"Not graded by evidence: {', '.join(names)}")
    return "\n".join(lines)


def load_calibration_block(roots: list[Path], now: float | None = None) -> str:
    """The block from the first `.breadcrumbs.yaml` found under ``roots``, or "" when there is none
    or it cannot be read. A missing or unreadable file yields nothing rather than a guess."""
    for root in roots:
        path = Path(root) / ".breadcrumbs.yaml"
        if not path.is_file():
            continue
        try:
            import yaml

            config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return ""
        try:
            return format_calibration_block(config, now)
        except Exception:
            return ""  # an odd file must cost the block, never the SessionStart or PostCompact output
    return ""
