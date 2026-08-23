#!/usr/bin/env python3
"""Measure what `empirica project-search` can and cannot do, so retrieval changes
stop being vibes.

WHY THIS EXISTS
---------------
Two separate defects were measured by hand on 2026-08-22 and neither is visible
from ordinary use:

  A. project-search is NEVER SILENT. It returns a full top-k with plausible
     scores for ANY input, including deliberate gibberish. There is no way for a
     response to say "nothing here matched", so every query looks answered.

  B. Recall collapses under paraphrase. Facts CERTAINLY present were retrieved
     by near-verbatim queries far more often than by the off-phrasing in which a
     question actually arrives.

They need opposite fixes and they interact badly: fixing A alone converts silent
confabulation into loud unhelpfulness, because the queries it would newly report
as empty are mostly ones where the answer IS in the graph. So both get measured
in one pass, by one harness, and neither number moves without the other visible
beside it.

WHAT IT REPORTS
---------------
  noise_floor   per band, from queries that cannot have an answer. Whatever a
                collection scores here is what it scores on nothing at all.
  recall        verbatim vs off-phrasing, matched on EXACT artifact id.
  separation    whether any score cut could split real hits from the floor, and
                with how much margin. This is the input to a threshold decision
                and deliberately NOT a threshold recommendation.

READING THE SCORES
------------------
Every score is POST-BOOST. empirica's search multiplies raw cosine by a
per-collection weight (`_COLLECTION_BOOST` in empirica/core/qdrant/memory.py:
memory x1.2, docs x0.5, episodic x0.9, ...). A threshold set from these numbers
must be applied on the same boosted scale, or it lands somewhere else entirely.
Bands are NOT comparable to each other for the same reason — each gets its own
floor, and a single global cut would be wrong by construction.

RANK, NOT JUST HIT
------------------
Misses record whether the target appeared anywhere in a widened sweep. A target
sitting just past the cut is fixed by a bigger k; a target absent from a wide
sweep is a genuine retrieval failure needing hybrid/lexical search. The boolean
cannot tell those apart and they have different fixes.

PROVENANCE
----------
Written by empirica-cortex (`scripts/retrieval_calibration.py` @ 255f79af), who
measured the two defects above and offered the harness with the report. Adopted
into core unchanged in structure — core owns `memory.py` and had no way to show a
before/after without it. Core's fixture keeps cortex's `nonsense` queries VERBATIM
so floors stay comparable across both graphs; only `probes` differ, because they
have to name artifacts that exist here.

CONFIRMATION
------------
Core added `confirmed` / `lexical` reporting, because the fix for A is a lexical
confirmation pass rather than a score cut — cortex's own measurement ruled a score
cut out, with true hits sitting BELOW the noise floor. A harness that only reports
scores cannot see whether that fix worked.

USAGE
    python3 scripts/retrieval_calibration.py [--fixture PATH] [--limit N]
                                             [--wide N] [--out PATH]
Run from a directory whose active empirica project owns the fixture's artifacts.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import subprocess
import sys

DEFAULT_FIXTURE = pathlib.Path(__file__).with_name("retrieval_calibration_fixture.json")


def resolve_binary(explicit: str | None) -> str:
    """Which `empirica` to measure — and it is NOT always the one on PATH.

    This cost a wrong reading during the very change the harness was written for.
    `empirica` on PATH resolved to a pipx venv holding a released build, while
    `import empirica` in the same shell resolved to the editable working tree. The
    harness shelled out, so it measured the OLD code and reported it as the new
    result: a run that looks like "the fix did nothing" and a run against
    unmodified code are the same output.

    So: prefer the binary sitting beside the interpreter that imports the package
    under test, fall back to PATH, and report the choice in `config` either way.
    An unstated binary is an unstated experiment.
    """
    if explicit:
        return explicit
    beside = pathlib.Path(sys.executable).with_name("empirica")
    return str(beside) if beside.exists() else "empirica"


def search(query: str, limit: int, binary: str = "empirica") -> dict[str, list[dict]]:
    """One project-search call, normalised to {band: [result, ...]}."""
    proc = subprocess.run(
        [binary, "project-search", "--task", query, "--limit", str(limit), "--output", "json"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if proc.returncode != 0 and not proc.stdout.strip():
        raise RuntimeError(f"project-search failed: {proc.stderr[:300]}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"project-search returned non-JSON: {exc}") from exc
    bands = payload.get("results") or {}
    return {k: v for k, v in bands.items() if isinstance(v, list)}


def find_rank(bands: dict[str, list[dict]], artifact_id: str):
    """(band, 1-indexed rank, score) for an EXACT artifact_id match, else None.

    Exact, never substring. The hand-run this replaces matched on a distinctive
    token and scored one row as a hit on text that merely contained it.
    """
    for band, items in bands.items():
        for i, item in enumerate(items, start=1):
            if item.get("artifact_id") == artifact_id:
                return band, i, item.get("score")
    return None


def top_lexical(bands: dict[str, list[dict]]) -> float | None:
    """Highest lexical agreement anywhere in a result set, or None if unsignalled."""
    vals = [
        item["lexical"] for items in bands.values() for item in items if isinstance(item.get("lexical"), (int, float))
    ]
    return max(vals) if vals else None


def find_lexical(bands: dict[str, list[dict]], artifact_id: str) -> float | None:
    for items in bands.values():
        for item in items:
            if item.get("artifact_id") == artifact_id and isinstance(item.get("lexical"), (int, float)):
                return float(item["lexical"])
    return None


def any_confirmed(bands: dict[str, list[dict]]) -> bool | None:
    """Did ANY returned result carry lexical confirmation?

    None when no result carries the field at all — which is the pre-fix state and
    must be reported as *absent*, not as False. Folding "the signal does not exist"
    into "the signal said no" would make a broken build look like a working one.
    """
    seen = False
    for items in bands.values():
        for item in items:
            if "confirmed" in item:
                seen = True
                if item.get("confirmed"):
                    return True
    return False if seen else None


def top_scores(bands: dict[str, list[dict]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for band, items in bands.items():
        scores: list[float] = []
        for item in items:
            s = item.get("score")
            if isinstance(s, (int, float)):
                scores.append(float(s))
        if scores:
            out[band] = max(scores)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixture", type=pathlib.Path, default=DEFAULT_FIXTURE)
    ap.add_argument("--limit", type=int, default=5, help="results per band, matching the CLI default (5)")
    ap.add_argument(
        "--wide", type=int, default=50, help="widened sweep for misses — separates 'just past k' from 'absent'"
    )
    ap.add_argument("--out", type=pathlib.Path, help="write the JSON report here too")
    ap.add_argument("--binary", help="empirica executable to measure; default is the one beside sys.executable")
    args = ap.parse_args()

    binary = resolve_binary(args.binary)

    fixture = json.loads(args.fixture.read_text())
    probes, nonsense = fixture["probes"], fixture["nonsense"]

    # ── Noise floor ─────────────────────────────────────────────────────
    floor_samples: dict[str, list[float]] = {}
    lexical_floor: list[float] = []
    lexical_hits: list[float] = []
    nonsense_confirmed = 0
    nonsense_signal_present = False
    for q in nonsense:
        bands = search(q, args.limit, binary)
        for band, score in top_scores(bands).items():
            floor_samples.setdefault(band, []).append(score)
        conf = any_confirmed(bands)
        if conf is not None:
            nonsense_signal_present = True
            nonsense_confirmed += int(conf)
        lx = top_lexical(bands)
        if lx is not None:
            lexical_floor.append(lx)

    noise_floor = {
        band: {
            "n": len(v),
            "max": round(max(v), 4),
            "mean": round(statistics.fmean(v), 4),
            "min": round(min(v), 4),
        }
        for band, v in sorted(floor_samples.items())
    }

    # ── Recall ──────────────────────────────────────────────────────────
    rows, hit_scores = [], []
    for p in probes:
        row = {"id": p["id"]}
        for phrasing in ("verbatim", "off"):
            bands = search(p[phrasing], args.limit, binary)
            found = find_rank(bands, p["expect"])
            if found:
                band, rank, score = found
                row[phrasing] = {
                    "hit": True,
                    "band": band,
                    "rank": rank,
                    "score": round(score, 4) if score else None,
                    "confirmed": any_confirmed(bands),
                    "lexical": find_lexical(bands, p["expect"]),
                }
                if score:
                    hit_scores.append(score)
                lx = find_lexical(bands, p["expect"])
                if lx is not None:
                    lexical_hits.append(lx)
            else:
                # Distinguish "just past k" from "not retrievable at all".
                wide = find_rank(search(p[phrasing], args.wide, binary), p["expect"])
                row[phrasing] = {
                    "hit": False,
                    "in_wide_sweep": bool(wide),
                    "wide_rank": wide[1] if wide else None,
                    "diagnosis": (
                        "beyond_k — a larger limit would surface it"
                        if wide
                        else f"absent at k={args.wide} — genuine retrieval failure"
                    ),
                }
        rows.append(row)

    n = len(rows)
    v_hits = sum(r["verbatim"]["hit"] for r in rows)
    o_hits = sum(r["off"]["hit"] for r in rows)
    beyond_k = sum(1 for r in rows for ph in ("verbatim", "off") if not r[ph]["hit"] and r[ph]["in_wide_sweep"])

    # ── Separation ──────────────────────────────────────────────────────
    # Can ANY cut split true hits from the noise floor? Reported per band as a
    # margin, not as a recommended threshold: a cut chosen from one run of one
    # fixture would be a number wearing a threshold's clothing.
    separation = {}
    for band, f in noise_floor.items():
        band_hits = [
            r[ph]["score"]
            for r in rows
            for ph in ("verbatim", "off")
            if r[ph]["hit"] and r[ph].get("band") == band and r[ph].get("score")
        ]
        if band_hits:
            separation[band] = {
                "floor_max": f["max"],
                "weakest_true_hit": round(min(band_hits), 4),
                "margin": round(min(band_hits) - f["max"], 4),
                "separable": min(band_hits) > f["max"],
            }

    report = {
        "config": {
            "limit": args.limit,
            "wide": args.wide,
            "probes": n,
            "nonsense_queries": len(nonsense),
            "binary": binary,
        },
        "scores_are": "POST-BOOST (empirica _COLLECTION_BOOST); bands not mutually comparable",
        "noise_floor": noise_floor,
        "recall": {
            "verbatim": f"{v_hits}/{n}",
            "off_phrasing": f"{o_hits}/{n}",
            "fragility_gap": v_hits - o_hits,
            "misses_that_were_only_beyond_k": beyond_k,
        },
        "separation": separation,
        # The comparison the whole change turns on. The SCORE margin is negative on
        # every graph measured — true hits sit below gibberish — so a score cut
        # cannot exist. Reported side by side so the lexical margin is read as an
        # alternative that was measured, not asserted.
        "lexical_separation": (
            {
                "noise_floor_max": round(max(lexical_floor), 4),
                "weakest_true_hit": round(min(lexical_hits), 4),
                "margin": round(min(lexical_hits) - max(lexical_floor), 4),
                "separable": min(lexical_hits) > max(lexical_floor),
            }
            if lexical_floor and lexical_hits
            else {"signal": "absent"}
        ),
        # Defect A, measured directly rather than inferred from scores. The ideal is
        # 0 of N nonsense queries confirmed while true hits stay confirmed — a run
        # where BOTH fall is a filter that silenced the tool, not one that fixed it.
        "confirmation": (
            {
                "signal": "present",
                "nonsense_queries_with_a_confirmed_result": f"{nonsense_confirmed}/{len(nonsense)}",
                "true_hits_confirmed": sum(
                    1 for r in rows for ph in ("verbatim", "off") if r[ph]["hit"] and r[ph].get("confirmed")
                ),
                "true_hits_total": sum(1 for r in rows for ph in ("verbatim", "off") if r[ph]["hit"]),
            }
            if nonsense_signal_present
            else {
                "signal": "absent",
                "note": "no result carries a `confirmed` field — the tool has no way to say nothing matched",
            }
        ),
        "rows": rows,
    }

    text = json.dumps(report, indent=2)
    print(text)
    if args.out:
        args.out.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
