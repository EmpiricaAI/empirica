"""Prevention-currency measurement — the positive mirror of the blindspot regret loop.

Per ``docs/architecture/PREVENTION_MEASUREMENT_SPEC.md``. Emits ``prevention_events``
(migration 058) and detects preventions at POSTFLIGHT: an exposed anti-pattern
prior that was acknowledged and did NOT lead to a same-subject failure within the
observation window = a measured prevention (the inverse of a regret).

All fail-open — the prevention machinery must never affect the CHECK/POSTFLIGHT
loop it observes.
"""

from __future__ import annotations

from .detection import apply_prevention_detection
from .persist import (
    DEFAULT_WINDOW_S,
    aggregate_prevention_events,
    emit_prevention_exposure,
    read_prevention_events,
)

__all__ = [
    "DEFAULT_WINDOW_S",
    "aggregate_prevention_events",
    "apply_prevention_detection",
    "emit_prevention_exposure",
    "read_prevention_events",
]
