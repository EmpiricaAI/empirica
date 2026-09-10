"""Fixes from a win32 1.13.41 install log (issues.md, 2026-09-09).

The reporter marked every claim as observed / carried / unverified — which made
the file directly actionable, and is why several items here cite measurements
nobody on a Linux box could have produced. The platform-independent fixes are
tested here; the two hook-side gate fixes have their own tests in
test_sentinel_gate_between_transactions.py.
"""

from __future__ import annotations

import sys

# ─── instance id: win32 resolved to None, Sentinel went blind ──────────


def test_win32_resolves_an_instance_id_instead_of_none(monkeypatch):
    """win32 has no TMUX_PANE / TERM_SESSION_ID / WINDOWID / TTY, so every
    Windows install fell through the whole chain to None — PREFLIGHT could not
    resolve project_path and the Sentinel firewall was silently blind. The
    documented workaround (a global EMPIRICA_INSTANCE_ID) should not be the
    only way the platform works."""
    from empirica.utils import session_resolver as sr

    for var in ("TMUX_PANE", "TERM_SESSION_ID", "WINDOWID", "EMPIRICA_INSTANCE_ID", "CLAUDE_INSTANCE_ID"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(sr, "get_tty_key", lambda: None)
    monkeypatch.setattr(sys, "platform", "win32")

    monkeypatch.setenv("WT_SESSION", "aabbccdd-1122-3344-5566-77889900aabb")
    assert sr._resolve_physical_location() == "wt_" + "aabbccdd-1122-3344-5566-77889900aabb"[:16], (
        "Windows Terminal GUID not used"
    )

    monkeypatch.delenv("WT_SESSION")
    assert sr._resolve_physical_location() == "win32_default", (
        "bare win32 must resolve to a constant, not None — resolution beats isolation "
        "on a platform where None means nothing works"
    )


def test_non_windows_still_resolves_none_at_chain_end(monkeypatch):
    """The constant is a WIN32 fallback, not a universal one. On POSIX, None at
    the end of the chain is meaningful (legacy first-match behaviour) and other
    code branches on it."""
    from empirica.utils import session_resolver as sr

    for var in ("TMUX_PANE", "TERM_SESSION_ID", "WINDOWID", "WT_SESSION"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(sr, "get_tty_key", lambda: None)
    monkeypatch.setattr(sys, "platform", "linux")
    assert sr._resolve_physical_location() is None


# ─── unknown-resolve: the natural flag spelling must work ──────────────


def test_unknown_resolve_accepts_resolution_as_alias():
    """Every sibling verb calls this field `resolution` (finding-resolve
    --resolution, resolve-artifacts' resolution key), so guessing it here is
    the natural first attempt — and the failure mode was a bare usage dump.
    The measured cost of that friction on the reporting install was a
    permanently corrupted artifact: the retry hand-quoted prose with backticks
    and bash executed them."""
    import argparse

    from empirica.cli.parsers import checkpoint_parsers

    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command")
    checkpoint_parsers.add_checkpoint_parsers(sub)

    for flag in ("--resolution", "--resolved-by"):
        ns = p.parse_args(["unknown-resolve", "--unknown-id", "u1", flag, "answered"])
        assert ns.resolved_by == "answered", f"{flag} did not land in resolved_by"


# ─── MCP timeout on transaction verbs must carry the recovery protocol ─


def test_mcp_timeout_hint_names_the_double_open_hazard():
    """The transaction row commits BEFORE the slow retrieval tail, so an MCP
    timeout routinely reports failure on work that succeeded — measured on the
    win32 install: transaction open at age 128s while the tool had already
    said it failed. The natural retry double-opens. An error that withholds
    the recovery protocol manufactures the next incident."""
    import pathlib

    src = (pathlib.Path(__file__).parent.parent / "empirica-mcp" / "empirica_mcp" / "server.py").read_text()
    assert "resubmitting double-opens" in src, "MCP timeout error lost its recovery hint"
    assert '"preflight-submit", "check-submit", "postflight-submit"' in src, (
        "the hint must target the transaction verbs, where the row commits before the tail"
    )


# ─── default embed model must name its exact tag ───────────────────────


def test_default_ollama_model_tag_is_explicit():
    """Ollama's registry resolves bare `qwen3-embedding` to the largest build
    (7.6B, 4096d) while our dimension table assumes the 0.6B/1024d one. The
    mismatch raises loudly at embed time now — but a default that INVITES the
    mismatch on every fresh install is still wrong. Name the exact model."""
    from empirica.core.qdrant.embeddings import DEFAULT_MODELS, MODEL_DIMENSIONS

    tag = DEFAULT_MODELS["ollama"]
    assert ":" in tag, f"default ollama model {tag!r} is a bare name — the registry chooses the build, not us"
    assert MODEL_DIMENSIONS.get(tag) == 1024, "the explicit default tag must be a 1024d model per our dim table"


# ─── win32 stdout must be UTF-8 without an env var ─────────────────────


def test_win32_stream_reconfigure_is_wired_and_scoped():
    """cp1252 killed the human renderer AFTER the DB write committed, so
    successful operations looked failed. The fix must be at the entry point
    (an env var the install forgets is a landmine) and must be a no-op on
    other platforms."""
    from empirica.cli import cli_core

    class _Fake:
        def __init__(self):
            self.encoding = None

        def reconfigure(self, encoding):
            self.encoding = encoding

    fake_out, fake_err = _Fake(), _Fake()
    orig = (sys.platform, sys.stdout, sys.stderr)
    try:
        sys.stdout, sys.stderr = fake_out, fake_err
        sys.platform = "win32"
        cli_core._reconfigure_win32_streams()
        assert fake_out.encoding == "utf-8" and fake_err.encoding == "utf-8"

        fake_out.encoding = fake_err.encoding = None
        sys.platform = "linux"
        cli_core._reconfigure_win32_streams()
        assert fake_out.encoding is None and fake_err.encoding is None, "must not touch non-win32 streams"
    finally:
        sys.platform, sys.stdout, sys.stderr = orig


def test_reconfigure_survives_a_stream_that_cannot():
    """A console that cannot reconfigure must not crash the CLI at import-adjacent
    time — that would replace a cosmetic mojibake risk with a total failure."""
    from empirica.cli import cli_core

    class _Rigid:
        def reconfigure(self, encoding):
            raise OSError("console says no")

    orig = (sys.platform, sys.stdout, sys.stderr)
    try:
        sys.stdout = sys.stderr = _Rigid()
        sys.platform = "win32"
        cli_core._reconfigure_win32_streams()  # must not raise
    finally:
        sys.platform, sys.stdout, sys.stderr = orig
