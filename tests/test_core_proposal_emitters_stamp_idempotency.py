"""Every core path that emits a proposal must stamp an idempotency key.

W1a's emitter half shipped as CODE and was removed as TEACHING. `empirica mailbox
reply` stamps `payload.idempotency_key` via `mesh_content.idempotency_key` and is
the only emission path that does; a word-count compression of the mesh skill
deleted the guidance that would tell an emitter the protection exists.

The measurement that surfaced it: peers found 220 emitter-supplied keys in
cortex's applied-keys ledger, four of them in one day, three from this practice —
and nobody could account for setting any. They had not. The CLI ack path stamps
silently. Behaviour shipping without vocabulary is harder to see than the reverse,
because the protected path gives no signal that it is protecting anything.

`cortex_propose` and `cortex_collab` are cortex's MCP tools; core cannot stamp a
payload it never builds, and that boundary is recorded in `idempotency_key`'s
docstring. What core CAN hold is its own side: if a second emitting path is ever
added here, it must not ship unprotected by omission.

A docstring cannot enforce that. This can.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "empirica"

# The POST that creates a proposal on cortex. Matching the PATH rather than a
# function name: a new emitter will be a new function with a name nobody can
# predict, but it cannot avoid this endpoint.
_PROPOSE_ENDPOINT = re.compile(r"/v1/orchestration/propose")


def _emitting_files() -> list[Path]:
    return sorted(
        p
        for p in _SRC.rglob("*.py")
        if "test" not in p.name and _PROPOSE_ENDPOINT.search(p.read_text(encoding="utf-8", errors="ignore"))
    )


def test_the_scan_finds_the_emitter_we_know_about():
    """Guards the guard.

    If the endpoint string is ever refactored behind a constant, this scan finds
    nothing and every assertion below passes vacuously — a green check over an
    empty set, which is the exact shape this whole area keeps producing.
    """
    found = _emitting_files()

    assert found, "no proposal emitter found — the scan is looking for the wrong thing"
    assert any(p.name == "mailbox_commands.py" for p in found), f"expected the known emitter among {found}"


@pytest.mark.parametrize("path", _emitting_files(), ids=lambda p: p.name)
def test_every_emitter_stamps_an_idempotency_key(path: Path):
    src = path.read_text(encoding="utf-8")

    assert "idempotency_key" in src, (
        f"{path.relative_to(_ROOT)} POSTs to /v1/orchestration/propose without stamping "
        "payload.idempotency_key. A retry after an unknown outcome would then duplicate "
        "the action rather than collapse at cortex's applied-keys ledger. Use "
        "empirica.core.mesh_content.idempotency_key, or declare payload.idempotent = False "
        "if the action is genuinely un-dedupable — but do not omit both silently."
    )


def test_the_helper_records_which_paths_are_protected():
    """The scope note is load-bearing, not decoration.

    Its absence is what let four practices spend a day unable to explain keys
    appearing in a ledger they had not knowingly written to. A reader arriving at
    this helper must learn that the protection is partial, and where the boundary
    runs — the CLI ack path stamps, the MCP tools do not.
    """
    from empirica.core.mesh_content import idempotency_key

    doc = (idempotency_key.__doc__ or "").lower()

    assert "mailbox reply" in doc, "the protected path must be named"
    assert "cortex_propose" in doc and "cortex_collab" in doc, "the unprotected paths must be named"
