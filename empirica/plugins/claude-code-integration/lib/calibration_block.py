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

import time
from datetime import datetime
from pathlib import Path

#: A vector is worth naming only when the evidence disagrees with the self-assessment by this much.
MIN_DIVERGENCE = 0.10
MAX_VECTORS = 6
MAX_INSIGHTS = 3
#: Past this age the profile is shown but labelled stale.
STALE_AFTER_DAYS = 7


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
    divergence = {k: float(v) for k, v in div.items() if isinstance(v, (int, float))} if isinstance(div, dict) else {}
    if not divergence:
        return ""

    now = time.time() if now is None else now
    age = _age_days(cal.get("last_updated"), now)
    when = str(cal.get("last_updated", "unknown date"))[:10]
    stale = f", STALE ({age:.0f} days old)" if age is not None and age > STALE_AFTER_DAYS else ""
    n = cal.get("observations")
    header = f"### Calibration (grounded{f', {n:,} observations' if isinstance(n, int) else ''}, updated {when}{stale})"

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
    insights.sort(key=lambda i: -float(i.get("severity") or 0))
    for i in insights[:MAX_INSIGHTS]:
        lines.append(f"  - {i['description']}")

    ungrounded = cal.get("ungrounded")
    if isinstance(ungrounded, list) and ungrounded:
        lines.append(f"Not graded by evidence: {', '.join(str(u) for u in ungrounded)}")
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
        return format_calibration_block(config, now)
    return ""
