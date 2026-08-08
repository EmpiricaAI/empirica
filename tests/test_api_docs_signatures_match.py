"""Documented signatures must match the real definitions, not just the names.

`test_api_docs_symbols_exist.py` asks *does this NAME appear in any `def`
anywhere?* — which is strictly weaker than what a reference page claims. It has
no class binding and no parameter comparison, so a doc can name a real function
and describe parameters it never had while CI stays green.

It did. Measured 2026-08-08 after the 43 phantom functions were removed: **20 of
62 documented signatures carried wrong parameter lists**, 0 missing. Three kinds,
only the first benign:

- **lagging** — the function grew optional params the page missed. Copying works,
  you just get less than the API offers.
- **wrong name** — `task_id` for `subtask_id` throughout the logging surface; a
  `tags=[...]` parameter documented three times and present in every worked
  example that never existed; `resolve_unknown(resolution, resolution_method)`
  against a real `(unknown_id, resolved_by, resolution_finding_id)`. Copied code
  raises `TypeError` on the first call.
- **wrong binding** — module-level functions documented as methods with `self`.

Deliberately NOT gated: annotations and defaults. Doc prose legitimately
simplifies `Optional[Dict[str, Any]]`, and a guard that fails on formatting gets
muted rather than fixed — which would cost the parameter check too.
"""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
API_DOCS = ROOT / "docs" / "reference" / "api"

# `### \`name(args) -> ret\`` — the heading form these pages use.
_HEADING = re.compile(r"^#+\s*`?([a-z_]\w*)\((.*?)\)(?:\s*->\s*(.+?))?`?\s*$", re.M)

# Documented signatures whose parameters do not match any real definition.
#
# EMPTY, and it may only shrink. The 20 found on 2026-08-08 were fixed in
# 59c5fbf71 rather than frozen, so an entry appearing here means new drift — a
# CI failure, not a debt to record.
KNOWN_SIGNATURE_DRIFT: dict[str, set[str]] = {}


def _real_signatures() -> dict[str, list[list[str]]]:
    """name -> every definition's parameter-name list, across the shipped packages."""
    out: dict[str, list[list[str]]] = defaultdict(list)
    files = list((ROOT / "empirica").rglob("*.py"))
    mcp = ROOT / "empirica-mcp"
    if mcp.exists():
        files += list(mcp.rglob("*.py"))
    for py in files:
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="ignore"))
        except (SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out[node.name].append(_param_names(node))
    return out


def _param_names(node) -> list[str]:
    a = node.args
    names = [p.arg for p in (*a.posonlyargs, *a.args)]
    if a.vararg:
        names.append("*" + a.vararg.arg)
    names += [p.arg for p in a.kwonlyargs]
    if a.kwarg:
        names.append("**" + a.kwarg.arg)
    return names


def _documented_params(argstr: str) -> list[str]:
    """Parameter names from a documented arg string, annotations stripped.

    Splits on TOP-LEVEL commas only — annotations contain them
    (`Dict[str, Any]`), and naive splitting would invent parameters.
    """
    if not argstr.strip():
        return []
    parts, depth, cur = [], 0, ""
    for ch in argstr:
        if ch in "[({":
            depth += 1
        elif ch in "])}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)

    names = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if p.startswith("**"):
            names.append("**" + p[2:].split(":")[0].split("=")[0].strip())
        elif p.startswith("*"):
            star = p[1:].split(":")[0].split("=")[0].strip()
            if star:
                names.append("*" + star)
        else:
            names.append(p.split(":")[0].split("=")[0].strip())
    return [n for n in names if n]


def _documented_entries(doc: Path):
    for name, argstr, _ret in _HEADING.findall(doc.read_text(encoding="utf-8")):
        yield name, _documented_params(argstr)


@pytest.fixture(scope="module")
def real() -> dict[str, list[list[str]]]:
    return _real_signatures()


@pytest.mark.parametrize("doc", sorted(API_DOCS.glob("*.md")), ids=lambda p: p.name)
def test_documented_parameters_match_a_real_definition(doc: Path, real):
    """Generous by construction: a name defined several times counts as correct
    if the doc matches ANY definition.

    That understates drift on purpose — what it reports is a floor. A guard that
    over-reports gets argued with; one that under-reports and still fires is
    believed.
    """
    wrong = []
    for name, documented in _documented_entries(doc):
        candidates = real.get(name)
        if not candidates:
            continue  # absence is the other guard's job
        if not any(c == documented for c in candidates):
            best = min(candidates, key=lambda c: abs(len(c) - len(documented)))
            wrong.append(f"{name}: documented {documented} — closest real {best}")

    unexpected = [w for w in wrong if w.split(":")[0] not in KNOWN_SIGNATURE_DRIFT.get(doc.name, set())]
    assert not unexpected, (
        f"{doc.name} documents parameters that match no real definition:\n  " + "\n  ".join(unexpected) + "\n"
        "Either the signature changed (fix the doc) or the doc was never right. "
        "Copied code raises TypeError on the first call."
    )


@pytest.mark.parametrize("doc_name", sorted(KNOWN_SIGNATURE_DRIFT))
def test_the_drift_inventory_only_shrinks(doc_name: str, real):
    """The direction that makes an inventory worth having: once an entry is
    fixed it must be removed, so the list cannot quietly become permanent."""
    doc = API_DOCS / doc_name
    documented = dict(_documented_entries(doc))
    still_wrong = set()
    for name in KNOWN_SIGNATURE_DRIFT[doc_name]:
        cands = real.get(name)
        if cands and not any(c == documented.get(name) for c in cands):
            still_wrong.add(name)
    fixed = KNOWN_SIGNATURE_DRIFT[doc_name] - still_wrong
    assert not fixed, f"{doc_name}: {sorted(fixed)} now match — remove them from KNOWN_SIGNATURE_DRIFT"


def test_the_inventory_is_empty_and_should_stay_that_way():
    """The 20 found on 2026-08-08 were FIXED, not frozen. An entry here is new
    drift; treat adding one as a decision, not bookkeeping."""
    assert KNOWN_SIGNATURE_DRIFT == {}


# ─── Negative controls: the guard must actually fire ───────────────────


def test_a_wrong_parameter_list_is_detected(real, tmp_path):
    """Without this the whole file could pass by parsing nothing."""
    doc = tmp_path / "fake.md"
    doc.write_text("### `log_finding(self, project_id: str, task_id: str) -> str`\n")
    documented = dict(_documented_entries(doc))
    assert documented["log_finding"] == ["self", "project_id", "task_id"]
    assert not any(c == documented["log_finding"] for c in real["log_finding"]), (
        "`task_id` is the real historical defect — it is `subtask_id` — so this must not match"
    )


def test_a_correct_parameter_list_is_accepted(real, tmp_path):
    """The other direction: the parser must not report drift on a right answer."""
    sig = ", ".join(real["log_finding"][0])
    doc = tmp_path / "ok.md"
    doc.write_text(f"### `log_finding({sig}) -> str`\n")
    documented = dict(_documented_entries(doc))
    assert any(c == documented["log_finding"] for c in real["log_finding"])


def test_annotations_containing_commas_do_not_invent_parameters(tmp_path):
    """`Dict[str, Any]` splits into two on a naive parser, which would report
    drift on every correctly-documented function that uses one."""
    doc = tmp_path / "ann.md"
    doc.write_text("### `f(self, a: Dict[str, Any] = None, b: Optional[List[str]] = None) -> str`\n")
    assert dict(_documented_entries(doc))["f"] == ["self", "a", "b"]


def test_the_guard_actually_reads_documented_entries():
    """A parser that silently matched nothing would make every assertion vacuous."""
    total = sum(len(list(_documented_entries(d))) for d in API_DOCS.glob("*.md"))
    assert total > 40, f"only {total} documented signatures parsed — the heading regex has drifted"
