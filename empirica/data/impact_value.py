"""One reading of the ``impact`` scale (0.0-1.0), for the code that reads it and the code that stores it.

The column is SQLite, so it holds whatever it is handed. Two legacy findings held explanatory prose, and the relevance
scorer multiplied them, raised, and took ``project-bootstrap`` (and with it the PreCompact hook) down (#415). Readers go
through :func:`read_impact`, which never raises; writers go through :func:`require_impact`, which refuses what a reader
could only guess at, so the malformed row is never created.
"""

from __future__ import annotations

import math

#: What an impact that cannot be used is read as: neither important nor ignorable.
NEUTRAL_IMPACT = 0.5


def _as_number(value) -> float | None:
    """The value as a finite float, or None. A bool is not a number here: True is not an impact of 1.0."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def read_impact(value, default: float = NEUTRAL_IMPACT) -> float:
    """Impact for scoring. A missing, non-numeric or non-finite value reads as ``default``; out-of-range clamps."""
    number = _as_number(value)
    if number is None:
        return default
    return max(0.0, min(1.0, number))


def require_impact(value) -> float | None:
    """Impact for storage: None stays None (the caller applies its default), a number becomes a float, anything else is
    refused. Raises ValueError so a batch can report the one bad node instead of storing prose."""
    if value is None:
        return None
    number = _as_number(value)
    if number is None:
        raise ValueError(f"impact must be a number between 0.0 and 1.0, got {str(value)[:80]!r}")
    return number
