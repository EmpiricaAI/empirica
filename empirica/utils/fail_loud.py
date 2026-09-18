"""Say why a best-effort step produced nothing, without making it fatal.

Much of POSTFLIGHT is best-effort by design: blindspot outcomes, prevention
detection, their persistence. Those steps must never fail the transaction, and
they used to meet that requirement with ``except Exception: return 0``. That
made a persistent failure (a schema drift, a renamed column, a locked db)
identical to "nothing to do" for as long as it lasted — and those counts are
training labels, so a silent failure is a silent hole in the data.

``warn_unless_missing_table`` keeps the step non-fatal and distinguishes the two
cases that are genuinely different: a table that does not exist yet (a fresh or
pre-migration store — quiet, nothing to report) and anything else (WARNING with
the cause, which the CLI prints on stderr).
"""

from __future__ import annotations

import logging

_ABSENT = ("no such table",)


def warn_unless_missing_table(logger: logging.Logger, where: str, exc: BaseException) -> None:
    """Log ``exc`` at WARNING unless it only says a table does not exist yet."""
    msg = str(exc).lower()
    if any(sig in msg for sig in _ABSENT):
        logger.debug("%s: table absent (fresh or pre-migration store): %s", where, exc)
        return
    logger.warning("%s FAILED — result is incomplete, not empty: %s: %s", where, type(exc).__name__, exc)
