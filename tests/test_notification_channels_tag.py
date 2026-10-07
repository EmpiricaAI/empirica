"""Tests for canonical-3-form tag resolution in the listener-on / channels path.

`resolve_orchestration_events_topic` must subscribe with the canonical
`<org>.<tenant>.<project>` tag (the same tag cortex publishes + `loop listen`
uses), not the bare slug — otherwise an in-session listener armed via
`listener on` matches no live ntfy push and only catches up via polling.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from empirica.core.cockpit import notification_channels as nc

# ── _canonical_tag ───────────────────────────────────────────────────


def test_canonical_tag_resolves_3form():
    with (
        patch.object(nc, "_cortex_creds", return_value=("https://cortex", "k")),
        patch(
            "empirica.core.loop_scheduler.content_poll._resolve_canonical_ai_id",
            return_value="empirica.david.empirica-autonomy",
        ) as r,
    ):
        assert nc._canonical_tag("empirica-autonomy") == "empirica.david.empirica-autonomy"
    r.assert_called_once_with("https://cortex", "k", "empirica-autonomy")


def test_canonical_tag_is_none_when_no_creds():
    with patch.object(nc, "_cortex_creds", return_value=None):
        assert nc._canonical_tag("empirica-autonomy") is None


def test_canonical_tag_is_none_on_resolver_error():
    with (
        patch.object(nc, "_cortex_creds", return_value=("https://cortex", "k")),
        patch(
            "empirica.core.loop_scheduler.content_poll._resolve_canonical_ai_id",
            side_effect=RuntimeError("roster down"),
        ),
    ):
        assert nc._canonical_tag("empirica-autonomy") is None


def test_canonical_tag_is_none_when_the_resolver_hands_back_the_bare_name():
    # _resolve_canonical_ai_id returns the basename unchanged on its own failures
    with (
        patch.object(nc, "_cortex_creds", return_value=("https://cortex", "k")),
        patch("empirica.core.loop_scheduler.content_poll._resolve_canonical_ai_id", return_value="empirica-autonomy"),
    ):
        assert nc._canonical_tag("empirica-autonomy") is None


# ── resolve_orchestration_events_topic builds ?tags=<3-form> ──────────


def test_topic_uses_canonical_tag():
    body = {"channels": [{"topic": "empirica-orchestration-events-david", "category": "orchestration_events"}]}
    with (
        patch.object(nc, "fetch_notification_channels", return_value=body),
        patch.object(nc, "_canonical_tag", return_value="empirica.david.empirica-autonomy") as ct,
    ):
        topic = nc.resolve_orchestration_events_topic("empirica-autonomy")
    assert topic == "ntfy:empirica-orchestration-events-david?tags=empirica.david.empirica-autonomy"
    ct.assert_called_once_with("empirica-autonomy")


def test_topic_still_raises_when_base_unresolvable():
    with (
        patch.object(nc, "fetch_notification_channels", return_value=None),
        pytest.raises(RuntimeError, match="orchestration-events topic"),
    ):
        nc.resolve_orchestration_events_topic("empirica-autonomy")


@pytest.mark.parametrize("handed_back", ["empirica.david", "empirica..x", "a.b.c.d", "", None])
def test_canonical_tag_rejects_anything_that_is_not_a_three_part_form(handed_back):
    with (
        patch.object(nc, "_cortex_creds", return_value=("https://cortex", "k")),
        patch("empirica.core.loop_scheduler.content_poll._resolve_canonical_ai_id", return_value=handed_back),
    ):
        assert nc._canonical_tag("empirica-autonomy") is None


def test_topic_refuses_to_subscribe_with_a_bare_tag_when_the_three_form_cannot_be_resolved():
    """David's ruling 2026-10-06: `listener on` refuses rather than arming a listener deaf to live events."""
    body = {"channels": [{"topic": "empirica-orchestration-events-david", "category": "orchestration_events"}]}
    with (
        patch.object(nc, "fetch_notification_channels", return_value=body),
        patch.object(nc, "_canonical_tag", return_value=None),
        pytest.raises(RuntimeError, match=r"canonical <org>\.<tenant>\.<project> address for 'empirica-autonomy'"),
    ):
        nc.resolve_orchestration_events_topic("empirica-autonomy")
