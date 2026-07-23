"""Tests for the cockpit→Claude loop install path.

Covers:
  - write_pending creates the file at the right path
  - render_loop_cron_prompt substitutes name/interval/description
  - consume_pending reads + deletes
  - LoopInstallRequest round-trips via from_path/to_dict
"""

from __future__ import annotations

import json

import pytest

from empirica.core.cockpit import loop_install_request as lir


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    fake_dir = tmp_path / ".empirica"
    fake_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(lir, "EMPIRICA_DIR", fake_dir)
    return fake_dir


class TestRenderPrompt:
    def test_substitutes_name_and_interval(self):
        prompt = lir.render_loop_cron_prompt(
            name="metrics-watch",
            interval="15m",
            description="watch the gauge",
        )
        assert "metrics-watch" in prompt
        assert '"15m"' in prompt
        assert "watch the gauge" in prompt

    def test_default_description_is_loop_specific(self):
        prompt = lir.render_loop_cron_prompt(name="foo", interval="5m")
        # No description passed — falls back to a name-derived label so
        # the rendered template doesn't ship empty-string quotes.
        assert "foo self-scheduling loop" in prompt

    def test_includes_self_scheduling_pattern(self):
        prompt = lir.render_loop_cron_prompt(name="x", interval="1h")
        assert "schedule-next" in prompt
        assert "CronCreate" in prompt
        assert "--result paused" in prompt


class TestPendingPath:
    def test_sanitizes_unsafe_chars(self, fake_home):
        # Same sanitization rule as sentinel-gate (matches writer/reader).
        p = lir.pending_path("tmux/foo%bar", "name")
        assert "tmux-foobar" in p.name
        assert "/" not in p.name[len("loop_install_pending_") :]


class TestWriteAndConsume:
    def test_write_creates_file_with_payload(self, fake_home):
        path = lir.write_pending(
            instance_id="tmux_3",
            name="loop-a",
            interval="15m",
            description="hello",
            requested_by="tmux_7",
        )
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["instance_id"] == "tmux_3"
        assert data["name"] == "loop-a"
        assert data["interval"] == "15m"
        assert data["description"] == "hello"
        assert data["requested_by"] == "tmux_7"
        assert "requested_at" in data
        assert "loop-a" in data["prompt_template"]

    def test_write_is_idempotent(self, fake_home):
        path1 = lir.write_pending(instance_id="tmux_3", name="loop-a", interval="15m")
        path2 = lir.write_pending(instance_id="tmux_3", name="loop-a", interval="30m")
        assert path1 == path2
        # Second write overwrites — interval reflects the latest value.
        data = json.loads(path1.read_text())
        assert data["interval"] == "30m"

    def test_consume_reads_and_deletes(self, fake_home):
        lir.write_pending(instance_id="tmux_3", name="a", interval="15m")
        lir.write_pending(instance_id="tmux_3", name="b", interval="30m")
        # Non-target instance should be ignored by consume.
        lir.write_pending(instance_id="tmux_99", name="c", interval="1h")

        consumed = lir.consume_pending("tmux_3")
        names = sorted(r.name for r in consumed)
        assert names == ["a", "b"]
        # Files for tmux_3 are gone.
        assert not lir.pending_path("tmux_3", "a").exists()
        assert not lir.pending_path("tmux_3", "b").exists()
        # tmux_99's request still there.
        assert lir.pending_path("tmux_99", "c").exists()

    def test_consume_returns_empty_when_none(self, fake_home):
        assert lir.consume_pending("tmux_42") == []

    def test_consume_tolerates_malformed_json(self, fake_home):
        path = lir.pending_path("tmux_3", "bad")
        path.write_text("not-json{")
        # Should not raise; deletes the broken file silently.
        result = lir.consume_pending("tmux_3")
        assert result == []
        assert not path.exists()


class TestRoundTrip:
    def test_from_path_round_trip(self, fake_home):
        path = lir.write_pending(
            instance_id="tmux_3",
            name="loop-a",
            interval="15m",
            description="hi",
            requested_by="tmux_7",
        )
        request = lir.LoopInstallRequest.from_path(path)
        assert request is not None
        assert request.instance_id == "tmux_3"
        assert request.name == "loop-a"
        assert request.requested_by == "tmux_7"
        assert request.scheduler_kind == lir.DEFAULT_SCHEDULER_KIND

    def test_from_path_missing_file_returns_none(self, fake_home):
        assert (
            lir.LoopInstallRequest.from_path(
                fake_home / "does_not_exist.json",
            )
            is None
        )


class TestCronKindRendering:
    """Regression for prop_sno3etin: cron-kind loops must register with --cron
    (their real schedule), not fall back to a hardcoded --interval 15m."""

    def test_cron_loop_renders_cron_not_interval(self):
        prompt = lir.render_loop_cron_prompt(name="message-cleanup", cron="17 3 * * *", description="daily cleanup")
        reg = next(l for l in prompt.splitlines() if "loop register" in l)
        assert '--cron "17 3 * * *"' in reg
        assert "15m" not in reg  # the old wrong fallback
        assert "--interval" not in reg

    def test_interval_loop_unchanged(self):
        prompt = lir.render_loop_cron_prompt(name="poll", interval="30s", description="poll")
        reg = next(l for l in prompt.splitlines() if "loop register" in l)
        assert '--interval "30s"' in reg
        assert "--cron" not in reg

    def test_write_pending_persists_cron_and_roundtrips(self, fake_home):
        path = lir.write_pending(instance_id="tmux_1", name="message-cleanup", cron="17 3 * * *", description="d")
        data = json.loads(path.read_text())
        assert data["cron"] == "17 3 * * *"
        assert data["interval"] is None
        back = lir.LoopInstallRequest.from_path(path)
        assert back is not None and back.cron == "17 3 * * *" and back.interval is None
