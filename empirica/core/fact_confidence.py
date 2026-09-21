"""The confidence a finding carries when it becomes an eidetic fact.

One formula, because three writers put a finding into the eidetic collection
(`finding-log` and `log-artifacts` at log time, `project-embed` and `rebuild` in
bulk) and they share a point id with first-writer-wins. Two formulas meant the
number depended on which verb ran first: impact 0.9 became 0.68 at log time and
0.90 from a re-embed, and memory promotion selects at 0.7.

The form is the bulk writers': the finding's impact, 0.6 when none was given.
That is what every practice logging through the batch path had been getting via
the session-end re-embed, so taking it changes the least. Kept outside
`empirica.core.qdrant` on purpose: importing that package loads the vector
client, and this is called from CLI handlers on the import-budget path.
"""

from __future__ import annotations

DEFAULT_FACT_CONFIDENCE = 0.6


def finding_fact_confidence(impact) -> float:
    """Confidence for a fact promoted from a finding with this impact."""
    try:
        value = float(impact)
    except (TypeError, ValueError):
        return DEFAULT_FACT_CONFIDENCE
    if not value:
        return DEFAULT_FACT_CONFIDENCE
    return min(max(value, 0.0), 1.0)
