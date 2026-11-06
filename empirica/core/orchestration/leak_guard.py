"""Credential shapes that must never ride in free text (proposals, notes, alert details), and the one table of them.

A proposal body, a completion note and an alert detail are all stored, indexed and READ BACK:
retrieved into other sessions' context, shown in a pane, promoted into shared memory. A key
written once is served again on every relevant query. The sweep that motivated this found a live
admin key in plaintext 43 times in five memory collections and a revoked one inside a
cortex-to-mesh-support proposal, so the boundary is worth a pattern.

Two tiers per shape, because two callers want opposite errors:

  * **exact**, for a gate. ``Proposal.validate`` refuses a proposal whose text matches, so a match
    must be unmistakably a credential: a false positive on prose blocks a message.
  * **loose**, for redaction (``empirica.core.redaction``). A miss there leaks a credential, so it
    errs the other way. ``redaction.py`` reads the loose tier from here; it scrubs exactly what it
    scrubbed before these shapes moved into this table.

The shapes:

  * ``ctx_<org>_<scope>_<32 hex>``   a Cortex API key; the ``ctx_test_*`` form is the fixture
                                      convention and is allowed by the gate.
  * ``tk_<24+ base32>``              an ntfy bearer token.
  * three base64url segments ``eyJ...``  a JWT (Cortex mints HS256 access tokens).

``find_secrets``, ``first_secret``, ``redact`` and ``scan_all`` and the ``[REDACTED:kind]`` marker
use the exact tier and are byte-compatible with Cortex's original module, which Cortex's tests pin.
Moved from Cortex (src/cortex/leak_guard.py) on David's ruling of 2026-11-06.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

#: name -> (exact pattern for a gate, loose pattern for redaction).
SHAPES: dict[str, tuple[re.Pattern[str], re.Pattern[str]]] = {
    "cortex_api_key": (
        re.compile(r"\bctx_[a-z0-9_]{1,12}?_[a-z]{3}_[0-9a-f]{32}\b"),
        re.compile(r"\bctx_[A-Za-z0-9_\-]{8,}"),
    ),
    "ntfy_token": (
        re.compile(r"\btk_[a-z0-9]{24,}\b"),
        re.compile(r"\btk_[A-Za-z0-9]{20,}"),
    ),
    "jwt": (
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
        re.compile(r"\beyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]+"),
    ),
}

#: The loose tier by name, for ``empirica.core.redaction``.
LOOSE: dict[str, re.Pattern[str]] = {name: loose for name, (_exact, loose) in SHAPES.items()}

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple((name, exact) for name, (exact, _loose) in SHAPES.items())
_ALLOWED = (re.compile(r"\bctx_test_[a-z]{3}_"), re.compile(r"\btk_[a-z]+_test_"))


def find_secrets(text: str | None) -> list[tuple[str, str]]:
    """``[(kind, matched text)]`` for every credential-shaped string in ``text``."""
    if not text or not isinstance(text, str):
        return []
    out: list[tuple[str, str]] = []
    for kind, rx in _PATTERNS:
        for m in rx.finditer(text):
            if any(a.search(m.group(0)) for a in _ALLOWED):
                continue
            out.append((kind, m.group(0)))
    return out


def first_secret(*texts: str | None) -> tuple[int, str] | None:
    """``(index of the text it was found in, kind)`` for the first credential-shaped string."""
    for i, t in enumerate(texts):
        found = find_secrets(t)
        if found:
            return i, found[0][0]
    return None


def redact(text: str | None) -> str | None:
    """``text`` with every credential-shaped string replaced by a visible marker. ``None`` stays
    ``None``. The marker names the kind and nothing of the value."""
    if not text or not isinstance(text, str):
        return text
    for kind, rx in _PATTERNS:
        text = rx.sub(
            lambda m, k=kind: m.group(0) if any(a.search(m.group(0)) for a in _ALLOWED) else f"[REDACTED:{k}]",
            text,
        )
    return text


def scan_all(values: Iterable[str | None]) -> list[str]:
    return [k for v in values for k, _ in find_secrets(v)]
