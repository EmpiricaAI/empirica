#!/usr/bin/env python3
"""Score several reviewer tiers that stamped the same threads, using pooled adjudication plus the mechanical check.

    python3 score_tiers.py RESULT.json TRACES_DIR

RESULT.json is the list of per-thread records a tier workflow returns:
    {"id": thread, "tiers": [...], "stamps": [stamp per tier], "pool": [{"id","tier","k"}], "adj": {"items": [{"id","supported","type_correct","cluster"}]}}
where "pool" lists every artifact of every tier anonymised as an id, "k" is its index in that tier's stamp, and the adjudicator
judged each id (supported: does the quote establish the claim; type_correct; cluster: same integer = same underlying thing).
TRACES_DIR holds <thread id>/turns.json (from build_trace.py).

Per tier it reports: artifacts, anchors that pass the mechanical check (judge-independent), precision (supported AND correctly
typed), supported share, share of supported artifacts that were mistyped, pooled recall (distinct supported clusters found
out of all supported clusters any tier found), and precision by artifact type. Pooled recall cannot see what NO tier found,
and an adjudicator that is also one of the tiers may favour its own family: the verbatim check is the only judge-independent figure.
"""

from __future__ import annotations

import collections
import importlib.util
import json
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("check_stamp", Path(__file__).with_name("check_stamp.py"))
check_stamp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_stamp)

TYPES = ["finding", "unknown", "assumption", "decision", "dead_end", "mistake"]


def score(records: list[dict], traces_dir: Path) -> dict:
    tiers = records[0]["tiers"]
    m = {t: collections.Counter() for t in tiers}
    by_type = {t: collections.defaultdict(collections.Counter) for t in tiers}
    for rec in records:
        turns = json.loads((Path(traces_dir) / rec["id"] / "turns.json").read_text())
        judged = {x["id"]: x for x in (rec.get("adj") or {"items": []})["items"]}
        pool_clusters = {x["cluster"] for x in judged.values() if x["supported"]}
        found = {t: set() for t in tiers}
        for p in rec["pool"]:
            j = judged.get(p["id"])
            if not j:
                continue
            tier = p["tier"]
            artifact = rec["stamps"][tiers.index(tier)]["artifacts"][p["k"]]
            good = j["supported"] and j["type_correct"]
            m[tier]["pooled"] += 1
            m[tier]["supported"] += j["supported"]
            m[tier]["good"] += good
            m[tier]["mistyped"] += j["supported"] and not j["type_correct"]
            by_type[tier][artifact["type"]]["n"] += 1
            by_type[tier][artifact["type"]]["good"] += good
            if j["supported"]:
                found[tier].add(j["cluster"])
        for tier in tiers:
            stamp = rec["stamps"][tiers.index(tier)]
            if not stamp:
                m[tier]["missing_stamps"] += 1
                continue
            report = check_stamp.check(stamp, turns)
            m[tier]["artifacts"] += report["artifacts"]
            m[tier]["anchors_valid"] += report["anchors_valid"]
            m[tier]["clusters_found"] += len(found[tier])
            m[tier]["clusters_pool"] += len(pool_clusters)
    out = {}
    for tier in tiers:
        c = m[tier]
        out[tier] = {
            "artifacts": c["artifacts"],
            "anchors_valid": c["anchors_valid"],
            "precision": c["good"] / c["pooled"] if c["pooled"] else None,
            "supported_share": c["supported"] / c["pooled"] if c["pooled"] else None,
            "mistyped_share_of_supported": c["mistyped"] / c["supported"] if c["supported"] else None,
            "pooled_recall": c["clusters_found"] / c["clusters_pool"] if c["clusters_pool"] else None,
            "by_type": {t: [by_type[tier][t]["good"], by_type[tier][t]["n"]] for t in TYPES if by_type[tier][t]["n"]},
            "missing_stamps": c["missing_stamps"],
        }
    return out


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    payload = json.loads(Path(args[0]).read_text())
    records = payload["result"] if isinstance(payload, dict) and "result" in payload else payload
    for tier, r in score(records, Path(args[1])).items():
        pct = lambda x: "-" if x is None else f"{x:.0%}"  # noqa: E731
        print(f"{tier:8} artifacts={r['artifacts']:4} anchors_valid={r['anchors_valid']:4} precision={pct(r['precision'])} "
              f"supported={pct(r['supported_share'])} pooled_recall={pct(r['pooled_recall'])} by_type={r['by_type']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
