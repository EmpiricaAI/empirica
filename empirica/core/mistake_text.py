"""The one place that knows how a mistake is written into an embedding, and read back.

A mistake is embedded as a single string so it can be searched semantically::

    MISTAKE: {mistake} Prevention: {prevention}

Three separate call sites built that string and two of them disagreed, while a
fourth parsed it back with a marker that did not match the one it tested for.
The result was #392: ``prevention`` persisted to SQLite correctly and came back
empty — or worse, as the four characters ``None`` — through PREFLIGHT/CHECK.

What actually went wrong, because each part is instructive:

1. ``m.get("prevention", "")`` returns **None**, not ``""``, when the key exists
   with a null value. The default only fires for a *missing* key, and 27 of 176
   mistakes had a present-but-NULL prevention. So the f-string rendered the
   literal ``"None"`` into the embedded text, and retrieval handed that back as
   the prevention — which is worse than empty, because it looks like content.

2. ``artifact_log_commands`` got this right (``prevention or 'none specified'``)
   while ``project_embed`` and ``rebuild`` did not. Same string, three authors,
   two behaviours — so whether a mistake surfaced usefully depended on which
   code path had last embedded it.

3. The reader tested ``"Prevention:" in text`` but split on ``"Prevention: "``
   *with* a trailing space. A text truncated immediately after the colon passes
   the test and then raises IndexError on ``[1]``, because splitting on a string
   that is not present returns a single-element list.

The fix is not better string handling at each site — it is having one site.
``prevention`` is the load-bearing field on a mistake artifact: the whole reason
to record a mistake is that the prevention resurfaces before you repeat it. A
mistake that surfaces without it is close to useless.
"""

from __future__ import annotations

_MARKER = " Prevention: "
_PREFIX = "MISTAKE: "

# Strings that have historically been embedded to mean "there is no prevention".
# Rows embedded before this module existed still carry them, and a re-embed is
# not something we can force on every practice, so the reader treats them as
# absent rather than as text.
_ABSENT_SENTINELS = {"none", "none specified", "n/a", "unknown", ""}


def build_mistake_text(mistake: str | None, prevention: str | None, *, prefix: bool = False) -> str:
    """Render a mistake for embedding.

    When there is no prevention the marker is **omitted entirely** rather than
    written with a placeholder. Absent and "the string 'None'" are different
    claims, and only one of them is true — a reader that gets no marker knows
    the field is missing, where a reader that gets ``"None"`` cannot tell that
    from a prevention someone actually typed.

    ``prefix`` adds the ``MISTAKE: `` prefix used by the live log path; the
    bulk re-embed paths historically omit it. Both parse identically.
    """
    body = (mistake or "").strip()
    text = f"{_PREFIX}{body}" if prefix else body

    cleaned = (prevention or "").strip()
    if cleaned and cleaned.lower() not in _ABSENT_SENTINELS:
        text = f"{text}{_MARKER}{cleaned}"
    return text


def parse_mistake_text(text: str | None) -> tuple[str, str]:
    """Split an embedded mistake back into ``(mistake, prevention)``.

    Never raises and never returns a sentinel as if it were content. A missing
    marker, a truncated one, or a historical ``"None"`` all yield an empty
    prevention — the honest answer, which the caller can act on.
    """
    raw = (text or "").strip()
    if not raw:
        return "", ""

    if raw.startswith(_PREFIX):
        raw = raw[len(_PREFIX) :]

    # Split on the colon rather than the full " Prevention: " marker, so a text
    # truncated right after the colon degrades to "no prevention" instead of
    # raising. Partition returns three parts always — no IndexError possible.
    head, sep, tail = raw.partition("Prevention:")
    if not sep:
        return raw.strip(), ""

    prevention = tail.strip()
    if prevention.lower() in _ABSENT_SENTINELS:
        prevention = ""
    return head.strip(), prevention
