"""Import-budget gate — hot-path entry points must not eagerly pull heavy deps.

Every `empirica <cmd>` pays `import empirica.cli`; the serve daemon pays
`import empirica.api.serve_app` on the `/health` hot path. Eagerly importing a
heavy dependency at module-load there taxes *every* invocation — and twice this
session it has bitten us:

  - #116: serve `/health` pulled `empirica.core.qdrant.embeddings`, which
    eagerly imported the openai SDK (~0.5s), tripping the 3.11 health-wait.
  - #119: the CLI pulled httpx (~190ms) + GitPython (~140ms).

Both were fixed by making those imports lazy. This is the forward-looking gate.

**The budget** is `_BUDGET`: per hot-path entry point, the set of heavy modules
it is *allowed* to pull (its real framework). Everything else in `_HEAVY` is
forbidden. A new heavy import in a hot path fails here; widening the budget is
then a deliberate, reviewed decision visible in the diff — a ratchet, not an
absolute bar.

**Presence-based, not time-based.** Import *time* is flaky on shared CI runners;
the *cause* we actually want to forbid (a heavy module landing in `sys.modules`)
is deterministic. So the budget is "which heavy modules loaded", measured in a
fresh subprocess. Runs in the normal suite → gates both CI and `release --prepare`.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

# Heavy / expensive modules that hot paths should import lazily, never eagerly.
# (LLM/embedding SDKs, network/server stacks, the vector store, ML + data libs,
# the web framework.) None of these are needed just to dispatch a CLI command.
_HEAVY = frozenset({
    "openai", "anthropic", "voyageai",                 # embedding / LLM SDKs
    "httpx", "uvicorn",                                # network / server
    "git",                                             # GitPython
    "qdrant_client",                                   # vector store
    "torch", "transformers", "sentence_transformers",  # ML
    "pandas", "numpy", "scipy", "sklearn",             # data / science
    "fastapi",                                         # web framework
})

# Per hot-path entry point: the heavy modules it is ALLOWED to pull (its genuine
# framework). Everything else in _HEAVY is forbidden for that entry point. This
# map IS the budget — expanding an entry's allow-set is a deliberate decision.
#
# `empirica.core.qdrant.embeddings` is intentionally NOT listed: it legitimately
# needs the vector stack (httpx/qdrant_client/numpy) and is not a universal hot
# path; its specific invariant (no openai SDK) lives in test_embeddings_no_openai_sdk.
_BUDGET: dict[str, frozenset[str]] = {
    "empirica.cli": frozenset(),                       # universal hot path — nothing heavy
    "empirica.api.serve_app": frozenset({"fastapi"}),  # FastAPI app — fastapi is its framework
}


def _modules_after(import_target: str) -> set[str]:
    """`sys.modules` keys after importing ``import_target`` in a fresh interpreter.

    Subprocess-isolated so prior imports in this test session can't mask a stray
    eager import.
    """
    code = (
        f"import {import_target}\n"
        "import sys, json\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"importing {import_target} failed:\n{proc.stderr}"
    return set(json.loads(proc.stdout.strip().splitlines()[-1]))


def _heavy_loaded(forbidden: frozenset[str], loaded: set[str]) -> list[str]:
    """Which forbidden top-level modules (or their submodules) are in `loaded`.

    Matches the top module exactly or any `mod.` submodule — so forbidding
    ``git`` (GitPython) never false-matches ``empirica.core.git``.
    """
    return sorted(
        m for m in forbidden
        if m in loaded or any(k == m or k.startswith(m + ".") for k in loaded)
    )


@pytest.mark.parametrize("entry_point", sorted(_BUDGET))
def test_hot_path_stays_within_import_budget(entry_point):
    allowed = _BUDGET[entry_point]
    forbidden = _HEAVY - allowed
    breached = _heavy_loaded(forbidden, _modules_after(entry_point))
    assert not breached, (
        f"{entry_point} eagerly imported heavy module(s) {breached} — keep them "
        f"lazy (import inside the function that needs them). If the cost is "
        f"genuinely intentional for this hot path, add the module to "
        f"_BUDGET['{entry_point}'] with a comment explaining why (that widens the "
        f"budget on purpose, in the diff)."
    )


def test_budget_entry_points_are_importable():
    """Sanity: every budgeted entry point actually imports (guards typos in the
    budget map — a non-importable key would silently never gate anything)."""
    for entry_point in _BUDGET:
        # _modules_after asserts returncode 0, i.e. the import succeeded.
        assert _modules_after(entry_point), entry_point
