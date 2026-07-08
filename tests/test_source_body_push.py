"""Tests for P2 sync-when-small body upload (_maybe_push_small_body gate).

The gate decides whether a source's body is uploaded to cortex on a
`sources-reconcile --apply --push-bodies` pass: small (<= threshold) +
readable local content + cortex configured. Best-effort, never raises.
"""

from __future__ import annotations

from unittest.mock import patch

import empirica.cli.command_handlers.sources_reconcile_commands as rc


def _row(size, path, mime="text/plain"):
    return {"id": "l1", "size_bytes": size, "canonical_path": path, "mime_type": mime}


def test_skips_when_too_large(tmp_path):
    f = tmp_path / "big.txt"
    f.write_text("x")
    row = _row(rc._SMALL_BODY_THRESHOLD + 1, str(f))
    assert rc._maybe_push_small_body("http://c", "k", "cx1", row) is None


def test_skips_when_no_local_path():
    assert rc._maybe_push_small_body("http://c", "k", "cx1", _row(10, None)) is None


def test_skips_when_no_cortex_config(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("hi")
    assert rc._maybe_push_small_body(None, None, "cx1", _row(2, str(f))) is None


def test_skips_when_file_missing(tmp_path):
    assert rc._maybe_push_small_body("http://c", "k", "cx1", _row(2, str(tmp_path / "nope.txt"))) is None


def test_skips_when_row_none():
    assert rc._maybe_push_small_body("http://c", "k", "cx1", None) is None


def test_uploads_small_body(tmp_path):
    f = tmp_path / "s.txt"
    f.write_bytes(b"hello")
    row = _row(5, str(f))
    with patch.object(
        rc,
        "_push_source_body_to_cortex",
        return_value={"pushed": True, "status": 200, "size_bytes": 5},
    ) as mock:
        res = rc._maybe_push_small_body("http://c", "k", "cx1", row)

    mock.assert_called_once()
    args = mock.call_args.args
    assert args[2] == "cx1"  # cortex_uuid
    assert args[3] == b"hello"  # file bytes read from canonical_path
    assert args[4] == "text/plain"  # mime_type
    assert res["pushed"] is True
    assert res["cortex_uuid"] == "cx1"  # stamped on the result


def test_push_helper_no_raise_on_network_error():
    """_push_source_body_to_cortex must never raise — returns a status dict."""
    res = rc._push_source_body_to_cortex(
        "http://127.0.0.1:9",  # nothing listening
        "k",
        "cx1",
        b"data",
        "text/plain",
    )
    assert res["pushed"] is False
    assert "error" in res
