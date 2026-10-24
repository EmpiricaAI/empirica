"""Listener relays allowlisted non-proposal wake shapes from the push body.

autonomy prop_tr4dbwcf: a non-proposal wake has no proposal-store row, so the
proposal-only catch-up can never reconstruct it — it was structurally undeliverable,
all-time-zero at every receiver. The listener relays allowlisted shapes directly from
the doorbell body BEFORE running catch-up. (The shape this was found with, the SER
escalation, is retired; `platform_dispatch_ready` is the allowlisted shape now.)
"""

from __future__ import annotations

import importlib.util as _ilu
import io
import json
import sys
from pathlib import Path


def _load():
    p = Path(__file__).resolve().parents[1] / "empirica/core/loop_scheduler/listener.py"
    spec = _ilu.spec_from_file_location("listener_mod", p)
    assert spec and spec.loader
    mod = _ilu.module_from_spec(spec)
    sys.modules["listener_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


_CANON = "empirica.david.empirica"


def _msg(body: dict) -> dict:
    return {"event": "message", "message": json.dumps(body)}


def _relay(mod, msg, canonical=_CANON):
    out, err = io.StringIO(), io.StringIO()
    relayed = mod._relay_non_proposal_wake(msg, "empirica", "cortex-mailbox-poll", canonical, out, err)
    return relayed, out.getvalue()


def test_relays_a_known_shape_with_its_fields():
    mod = _load()
    relayed, out = _relay(
        mod, _msg({"event": "platform_dispatch_ready", "dispatch_id": "d_abc", "platform": "linkedin"})
    )
    assert relayed is True
    line = json.loads(out.strip())
    assert line["event_type"] == "platform_dispatch_ready"
    assert line["dispatch_id"] == "d_abc"
    assert line["platform"] == "linkedin"
    assert line["instance_id"] == "empirica"
    assert "event" not in line  # collapsed into event_type


def test_proposal_shaped_body_not_relayed():
    # proposals are reconstructed by catch-up — the relay must NOT double-emit them.
    mod = _load()
    relayed, out = _relay(mod, _msg({"event": "proposal_event", "proposal_id": "p1"}))
    assert relayed is False
    assert out == ""


def test_wrong_target_not_relayed():
    mod = _load()
    relayed, _ = _relay(
        mod,
        _msg({"event": "platform_dispatch_ready", "dispatch_id": "x", "target_claudes": ["empirica.philipp.other"]}),
    )
    assert relayed is False


def test_matching_target_in_body_relayed():
    mod = _load()
    relayed, _ = _relay(mod, _msg({"event": "platform_dispatch_ready", "dispatch_id": "x", "target_claudes": [_CANON]}))
    assert relayed is True


def test_no_target_in_body_relayed_tag_filter_gated():
    # No body target_claudes → rely on the ntfy tag-filter subscription (already
    # recipient-gated). Relay.
    mod = _load()
    relayed, _ = _relay(mod, _msg({"event": "platform_dispatch_ready", "dispatch_id": "x"}))
    assert relayed is True


def test_non_json_body_is_safe_noop():
    mod = _load()
    relayed, out = _relay(mod, {"event": "message", "message": "keepalive-not-json"})
    assert relayed is False
    assert out == ""


def test_missing_message_field_is_safe_noop():
    mod = _load()
    relayed, out = _relay(mod, {"event": "message"})
    assert relayed is False
    assert out == ""


def test_unknown_event_shape_not_relayed():
    mod = _load()
    relayed, _ = _relay(mod, _msg({"event": "some_future_shape", "x": 1}))
    assert relayed is False  # only allowlisted shapes relay
