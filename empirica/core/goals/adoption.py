"""Adopting a SHARED goal's criteria into the locally-EVALUATED registry.

The shared surface (cortex) is a registry and transport of criteria, **never the
evaluator** — not a preference but because the evidence a criterion is graded
against (pytest results, git metrics, artifact counts) is seat-local by nature.
So a criterion written on the shared surface is stored honestly and checked by
nothing until a local seat adopts it. This module is that adoption.

Cortex stores criteria as free text; core validates a closed vocabulary. That
mismatch is a boundary, and boundaries are where silent degradation happens, so
the rules here are deliberately blunt:

**Never map an unparsed criterion to `completion`.** That is the exact defect
the vocabulary widening removed — `completion` applied to everything and so
produced a verdict on every criterion, which made goal-completion evidence
unfalsifiable at scale.

**Two labels, not one.** Cortex's argument, and it is right:

    undetermined   the AUTHOR never graded it. Legal at creation on the shared
                   surface. Remediation: re-run the grounding protocol.
    untranslated   the author graded it checkably and CORE'S PARSER could not
                   map it. Remediation: extend the vocabulary mapping here.

If both landed as `undetermined`, translator gaps would hide inside author gaps
permanently — nobody sweeps `undetermined` asking *which of these are my own
parser's fault*, so the mapping would never grow. Exemption-reports-clean-forever,
with the exemption self-inflicted.

`untranslated` is therefore also a BACKLOG, and `untranslated_backlog()` exists
so it can be listed. A backlog nobody can query is the same silence one layer on.
"""

from __future__ import annotations

import re
from typing import Any

from empirica.core.goals.validation import VALID_VALIDATION_METHODS

#: `method:metric@op:threshold`, e.g. `completion:subtask_ratio@>=1.0`.
TYPED = re.compile(r"^\s*(?P<method>\w+)\s*:\s*(?P<metric>[\w./-]+)\s*@\s*(?P<op>>=|<=|==|>|<)\s*(?P<value>[0-9.]+)")

#: `undetermined: <reason>` — the author's declared non-claim, cortex-legal.
DECLARED = re.compile(r"^\s*(?P<method>undetermined|prose)\s*:\s*(?P<rest>.+)", re.IGNORECASE)

#: Marks a criterion core's parser could not map. NOT in the authorable
#: vocabulary: a practitioner hand-writing it would be claiming core's parser
#: failed on something core's parser never saw, which is not a claim they are
#: positioned to make. Produced here, consumed by the backlog.
UNTRANSLATED = "untranslated"


def translate(raw: Any) -> dict[str, Any]:
    """One shared-surface criterion → a locally-storable criterion dict.

    Lossless in the direction that matters: the original text is always carried,
    so nothing a peer wrote is discarded by a parser that did not recognise it.
    """
    text = raw if isinstance(raw, str) else (raw.get("description") if isinstance(raw, dict) else None)
    text = (text or "").strip()

    if not text:
        return {
            "description": "",
            "validation_method": UNTRANSLATED,
            "original": raw,
            "reason": "empty criterion — nothing to translate",
        }

    # An already-structured criterion from a peer that speaks the vocabulary.
    if isinstance(raw, dict) and raw.get("validation_method") in VALID_VALIDATION_METHODS:
        return {
            "description": text,
            "validation_method": raw["validation_method"],
            "threshold": raw.get("threshold"),
            "original": raw,
        }

    m = TYPED.match(text)
    if m and m.group("method") in VALID_VALIDATION_METHODS:
        return {
            "description": m.group("metric"),
            "validation_method": m.group("method"),
            "threshold": float(m.group("value")),
            "original": text,
        }

    d = DECLARED.match(text)
    if d:
        # The author's own non-claim survives adoption AS a non-claim. Upgrading
        # it to something checkable would invent a grading they declined to make.
        return {
            "description": d.group("rest").strip(),
            "validation_method": d.group("method").lower(),
            "original": text,
        }

    # Parseable shape, unknown method — the sharpest case, because it is the one
    # a silent fallback would swallow. `tests_pass:...` from a peer running a
    # newer vocabulary lands HERE, and must be visible as MY gap.
    if m:
        return {
            "description": text,
            "validation_method": UNTRANSLATED,
            "original": text,
            "reason": f"unknown validation_method {m.group('method')!r} — extend core's vocabulary to adopt this",
        }

    return {
        "description": text,
        "validation_method": UNTRANSLATED,
        "original": text,
        "reason": "free-text criterion with no recognised method — extend the mapping or ask the author to re-state it",
    }


def adopt(criteria: list[Any] | None) -> dict[str, Any]:
    """Translate a shared goal's criteria and report the split honestly.

    The counts are the point. `adopted` alone would let a run where every
    criterion failed to translate read as a successful adoption.
    """
    items = [translate(c) for c in (criteria or [])]
    untranslated = [i for i in items if i["validation_method"] == UNTRANSLATED]
    declared = [i for i in items if i["validation_method"] in ("undetermined", "prose")]
    evaluable = [i for i in items if i not in untranslated and i not in declared]

    return {
        "criteria": items,
        "total": len(items),
        "evaluable": len(evaluable),
        "declared_unclaimable": len(declared),
        "untranslated": len(untranslated),
        # Named, not counted. A count tells an operator something is wrong; the
        # reasons tell them which vocabulary entry would fix it.
        "untranslated_reasons": [i.get("reason") for i in untranslated],
    }


def untranslated_backlog(criteria: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every criterion core could not map, as a listable backlog.

    This exists so the translator-gap remediation is TRIGGERABLE. Without a way
    to ask *which criteria did my parser fail on*, the mapping never grows and
    the gap becomes permanent while looking like ordinary author uncertainty.
    """
    return [
        {"description": c.get("description"), "original": c.get("original"), "reason": c.get("reason")}
        for c in criteria
        if c.get("validation_method") == UNTRANSLATED
    ]
