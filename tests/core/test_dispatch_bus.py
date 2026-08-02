"""Tests for empirica.core.dispatch_bus — typed cross-instance dispatch."""

import time
from unittest.mock import MagicMock

from empirica.core.dispatch_bus import (
    DispatchBus,
    DispatchMessage,
    DispatchPriority,
    DispatchResult,
    DispatchStatus,
    InstanceInfo,
    InstanceRegistry,
    get_global_bus,
    set_global_bus,
)

# ---------------------------------------------------------------------------
# DispatchMessage serialization
# ---------------------------------------------------------------------------


class TestDispatchMessageSerialization:
    def test_roundtrip_basic(self):
        original = DispatchMessage(
            action="test_action",
            from_instance="inst-1",
            to_instance="inst-2",
            payload={"key": "value"},
            correlation_id="corr-123",
        )
        body = original.to_message_body()
        parsed = DispatchMessage.from_message_body(body)
        assert parsed is not None
        assert parsed.action == "test_action"
        assert parsed.from_instance == "inst-1"
        assert parsed.to_instance == "inst-2"
        assert parsed.payload == {"key": "value"}
        assert parsed.correlation_id == "corr-123"

    def test_roundtrip_full(self):
        original = DispatchMessage(
            action="schedule_cron",
            from_instance="terminal-1",
            to_instance="cowork-1",
            payload={"command": "run X", "schedule": "0 9 * * *"},
            correlation_id="corr-456",
            priority=DispatchPriority.HIGH,
            deadline=time.time() + 3600,
            required_capabilities=["schedule", "cron"],
            callback_channel="dispatch",
            metadata={"tag": "test"},
        )
        body = original.to_message_body()
        parsed = DispatchMessage.from_message_body(body)
        assert parsed is not None
        assert parsed.priority == DispatchPriority.HIGH
        assert parsed.required_capabilities == ["schedule", "cron"]
        assert parsed.metadata == {"tag": "test"}

    def test_parse_invalid_json(self):
        assert DispatchMessage.from_message_body("not json") is None

    def test_parse_empty_body(self):
        parsed = DispatchMessage.from_message_body("{}")
        assert parsed is not None
        assert parsed.action == ""
        assert parsed.priority == DispatchPriority.NORMAL

    def test_is_expired_no_deadline(self):
        msg = DispatchMessage(action="x", from_instance="a", to_instance="b")
        assert not msg.is_expired()

    def test_is_expired_future(self):
        msg = DispatchMessage(
            action="x",
            from_instance="a",
            to_instance="b",
            deadline=time.time() + 3600,
        )
        assert not msg.is_expired()

    def test_is_expired_past(self):
        msg = DispatchMessage(
            action="x",
            from_instance="a",
            to_instance="b",
            deadline=time.time() - 60,
        )
        assert msg.is_expired()


class TestDispatchResultSerialization:
    def test_roundtrip(self):
        original = DispatchResult(
            correlation_id="corr-123",
            status=DispatchStatus.COMPLETED,
            from_instance="worker-1",
            payload={"result": 42},
            duration_ms=1500,
        )
        body = original.to_message_body()
        parsed = DispatchResult.from_message_body(body)
        assert parsed is not None
        assert parsed.correlation_id == "corr-123"
        assert parsed.status == DispatchStatus.COMPLETED
        assert parsed.payload == {"result": 42}
        assert parsed.duration_ms == 1500

    def test_result_with_error(self):
        original = DispatchResult(
            correlation_id="corr-456",
            status=DispatchStatus.FAILED,
            from_instance="worker-2",
            error="Something broke",
        )
        body = original.to_message_body()
        parsed = DispatchResult.from_message_body(body)
        assert parsed is not None
        assert parsed.status == DispatchStatus.FAILED
        assert parsed.error == "Something broke"

    def test_invalid_json(self):
        assert DispatchResult.from_message_body("not json") is None


# ---------------------------------------------------------------------------
# InstanceInfo
# ---------------------------------------------------------------------------


class TestInstanceInfo:
    def test_has_capability(self):
        info = InstanceInfo(
            instance_id="test",
            instance_type="cli",
            capabilities=["codebase", "git"],
        )
        assert info.has_capability("codebase")
        assert not info.has_capability("browser")

    def test_has_all_capabilities(self):
        info = InstanceInfo(
            instance_id="test",
            instance_type="cli",
            capabilities=["codebase", "git", "shell"],
        )
        assert info.has_all_capabilities(["codebase", "git"])
        assert not info.has_all_capabilities(["codebase", "browser"])
        assert info.has_all_capabilities([])  # Empty list always matches


# ---------------------------------------------------------------------------
# InstanceRegistry
# ---------------------------------------------------------------------------


class TestInstanceRegistry:
    def _temp_registry(self, tmp_path):
        # Takes tmp_path instead of calling mkdtemp(): these dirs were never
        # removed, and a peer's /tmp filling up surfaced as failures in
        # unrelated tests.
        return InstanceRegistry(path=tmp_path / "bus-instances.yaml")

    def test_register_new(self, tmp_path):
        reg = self._temp_registry(tmp_path)
        info = reg.register(
            instance_id="terminal-1",
            instance_type="claude-code-cli",
            capabilities=["codebase", "git"],
            subscribes=["dispatch.terminal"],
        )
        assert info.instance_id == "terminal-1"
        assert info.registered_at is not None
        assert info.last_seen is not None

    def test_register_persists_to_disk(self, tmp_path):
        path = tmp_path / "bus-instances.yaml"
        reg = InstanceRegistry(path=path)
        reg.register("t-1", "cli", ["a", "b"])
        assert path.exists()
        # Load fresh instance
        reg2 = InstanceRegistry(path=path)
        assert reg2.get("t-1") is not None
        assert reg2.get("t-1").capabilities == ["a", "b"]

    def test_unregister(self, tmp_path):
        reg = self._temp_registry(tmp_path)
        reg.register("t-1", "cli", ["a"])
        assert reg.get("t-1") is not None
        assert reg.unregister("t-1") is True
        assert reg.get("t-1") is None

    def test_unregister_missing(self, tmp_path):
        reg = self._temp_registry(tmp_path)
        assert reg.unregister("nonexistent") is False

    def test_list_all(self, tmp_path):
        reg = self._temp_registry(tmp_path)
        reg.register("t-1", "cli", ["a"])
        reg.register("t-2", "web", ["b"])
        all_instances = reg.list_all()
        assert len(all_instances) == 2

    def test_find_by_capability(self, tmp_path):
        reg = self._temp_registry(tmp_path)
        reg.register("t-1", "cli", ["codebase", "git"])
        reg.register("t-2", "web", ["browser", "gmail"])
        reg.register("t-3", "cli", ["codebase"])

        codebase_instances = reg.find_by_capability("codebase")
        assert len(codebase_instances) == 2

        browser_instances = reg.find_by_capability("browser")
        assert len(browser_instances) == 1
        assert browser_instances[0].instance_id == "t-2"

    def test_find_by_capabilities(self, tmp_path):
        reg = self._temp_registry(tmp_path)
        reg.register("t-1", "cli", ["codebase", "git", "shell"])
        reg.register("t-2", "cli", ["codebase", "git"])
        reg.register("t-3", "web", ["browser", "gmail"])

        matches = reg.find_by_capabilities(["codebase", "git"])
        assert len(matches) == 2

        matches = reg.find_by_capabilities(["codebase", "git", "shell"])
        assert len(matches) == 1

        matches = reg.find_by_capabilities(["browser", "gmail"])
        assert len(matches) == 1

    def test_touch_updates_last_seen(self, tmp_path):
        reg = self._temp_registry(tmp_path)
        reg.register("t-1", "cli", ["a"])
        original = reg.get("t-1").last_seen
        time.sleep(0.01)
        reg.touch("t-1")
        assert reg.get("t-1").last_seen > original

    def test_touch_missing(self, tmp_path):
        reg = self._temp_registry(tmp_path)
        # Should not raise
        reg.touch("nonexistent")


# ---------------------------------------------------------------------------
# DispatchBus (with mocked GitMessageStore)
# ---------------------------------------------------------------------------


class TestDispatchBus:
    def _bus(self, tmp_path):
        """Create a bus with mocked message store + temp registry."""
        mock_store = MagicMock()
        mock_store.send_message.return_value = "msg-id-123"
        mock_store.reply.return_value = "reply-id-456"
        mock_store.get_inbox.return_value = []
        reg = InstanceRegistry(path=tmp_path / "bus.yaml")
        return DispatchBus(
            instance_id="terminal-1",
            message_store=mock_store,
            registry=reg,
        )

    def test_register_self(self, tmp_path):
        bus = self._bus(tmp_path)
        info = bus.register_self(
            instance_type="claude-code-cli",
            capabilities=["codebase", "git"],
            subscribes=["dispatch.terminal"],
        )
        assert info.instance_id == "terminal-1"
        assert info.capabilities == ["codebase", "git"]
        assert bus.registry.get("terminal-1") is not None

    def test_dispatch_direct(self, tmp_path):
        bus = self._bus(tmp_path)
        corr_id = bus.dispatch(
            to_instance="cowork-1",
            action="schedule_cron",
            payload={"schedule": "0 9 * * *"},
        )
        assert corr_id is not None
        assert "schedule_cron" in corr_id
        assert bus.store.send_message.called

        # Verify the body was properly encoded
        call_args = bus.store.send_message.call_args
        body = call_args.kwargs["body"]
        parsed = DispatchMessage.from_message_body(body)
        assert parsed.action == "schedule_cron"
        assert parsed.payload == {"schedule": "0 9 * * *"}

    def test_dispatch_with_deadline(self, tmp_path):
        bus = self._bus(tmp_path)
        bus.dispatch(
            to_instance="cowork-1",
            action="quick_task",
            deadline_seconds=60,
        )
        call_args = bus.store.send_message.call_args
        body = call_args.kwargs["body"]
        parsed = DispatchMessage.from_message_body(body)
        assert parsed.deadline is not None
        assert parsed.deadline > time.time()
        assert parsed.deadline <= time.time() + 60.1

    def test_dispatch_capability_routing(self, tmp_path):
        bus = self._bus(tmp_path)
        bus.registry.register("web-1", "cowork-web", ["browser", "gmail"])
        bus.registry.register("web-2", "cowork-web", ["browser"])

        corr_id = bus.dispatch(
            to_instance="*",
            action="send_email",
            required_capabilities=["gmail"],
        )
        assert corr_id is not None

        # Should have routed to web-1 (the one with gmail)
        call_args = bus.store.send_message.call_args
        assert call_args.kwargs["to_ai_id"] == "web-1"

    def test_dispatch_capability_no_match(self, tmp_path):
        bus = self._bus(tmp_path)
        corr_id = bus.dispatch(
            to_instance="*",
            action="some_action",
            required_capabilities=["nonexistent"],
        )
        assert corr_id is None

    def test_send_result(self, tmp_path):
        bus = self._bus(tmp_path)
        result_id = bus.send_result(
            original_message_id="orig-msg",
            original_channel="dispatch",
            correlation_id="corr-123",
            status=DispatchStatus.COMPLETED,
            payload={"result": "ok"},
            duration_ms=500,
        )
        assert result_id is not None
        assert bus.store.reply.called

        # Verify result body
        call_args = bus.store.reply.call_args
        body = call_args.kwargs["body"]
        parsed = DispatchResult.from_message_body(body)
        assert parsed.correlation_id == "corr-123"
        assert parsed.status == DispatchStatus.COMPLETED

    def test_poll_inbox_empty(self, tmp_path):
        bus = self._bus(tmp_path)
        bus.store.get_inbox.return_value = []
        dispatches = bus.poll_inbox()
        assert dispatches == []

    def test_poll_inbox_with_messages(self, tmp_path):
        bus = self._bus(tmp_path)
        # Create a test dispatch message and simulate it in the inbox
        test_dispatch = DispatchMessage(
            action="test_action",
            from_instance="sender",
            to_instance="terminal-1",
            payload={"key": "val"},
            correlation_id="test-corr",
        )
        bus.store.get_inbox.return_value = [
            {
                "message_id": "m-1",
                "channel": "dispatch",
                "body": test_dispatch.to_message_body(),
                "type": "request",
            }
        ]

        dispatches = bus.poll_inbox()
        assert len(dispatches) == 1
        assert dispatches[0].action == "test_action"
        assert dispatches[0].metadata["_message_id"] == "m-1"

    def test_poll_inbox_skips_expired(self, tmp_path):
        bus = self._bus(tmp_path)
        expired_dispatch = DispatchMessage(
            action="old",
            from_instance="s",
            to_instance="terminal-1",
            deadline=time.time() - 100,
        )
        bus.store.get_inbox.return_value = [
            {
                "message_id": "m-1",
                "channel": "dispatch",
                "body": expired_dispatch.to_message_body(),
                "type": "request",
            }
        ]

        dispatches = bus.poll_inbox(include_expired=False)
        assert len(dispatches) == 0

    def test_poll_inbox_skips_malformed(self, tmp_path):
        bus = self._bus(tmp_path)
        bus.store.get_inbox.return_value = [
            {
                "message_id": "m-1",
                "channel": "dispatch",
                "body": "not valid json",
                "type": "request",
            }
        ]
        dispatches = bus.poll_inbox()
        assert len(dispatches) == 0

    def test_poll_results(self, tmp_path):
        bus = self._bus(tmp_path)
        result = DispatchResult(
            correlation_id="corr-xyz",
            status=DispatchStatus.COMPLETED,
            from_instance="worker",
            payload={"data": 1},
        )
        bus.store.get_inbox.return_value = [
            {
                "message_id": "r-1",
                "body": result.to_message_body(),
                "type": "response",
            }
        ]

        results = bus.poll_results()
        assert len(results) == 1
        assert results[0].correlation_id == "corr-xyz"

    def test_poll_results_filter_by_correlation(self, tmp_path):
        bus = self._bus(tmp_path)
        r1 = DispatchResult(correlation_id="a", status=DispatchStatus.COMPLETED, from_instance="w")
        r2 = DispatchResult(correlation_id="b", status=DispatchStatus.COMPLETED, from_instance="w")
        bus.store.get_inbox.return_value = [
            {"message_id": "r-1", "body": r1.to_message_body(), "type": "response"},
            {"message_id": "r-2", "body": r2.to_message_body(), "type": "response"},
        ]

        results = bus.poll_results(correlation_id="b")
        assert len(results) == 1
        assert results[0].correlation_id == "b"

    def test_poll_results_skips_requests(self, tmp_path):
        bus = self._bus(tmp_path)
        # Only requests in the inbox, not responses
        d = DispatchMessage(action="x", from_instance="s", to_instance="terminal-1")
        bus.store.get_inbox.return_value = [
            {
                "message_id": "m-1",
                "body": d.to_message_body(),
                "type": "request",
            }
        ]
        results = bus.poll_results()
        assert len(results) == 0

    def test_handle_dispatch_success(self, tmp_path):
        bus = self._bus(tmp_path)
        dispatch = DispatchMessage(
            action="test",
            from_instance="s",
            to_instance="terminal-1",
            correlation_id="c-1",
            metadata={"_message_id": "orig", "_channel": "dispatch"},
        )

        def handler(d: DispatchMessage):
            return DispatchStatus.COMPLETED, {"result": "ok"}, None

        reply_id = bus.handle_dispatch(dispatch, handler)
        assert reply_id is not None
        assert bus.store.reply.called

    def test_handle_dispatch_failure(self, tmp_path):
        bus = self._bus(tmp_path)
        dispatch = DispatchMessage(
            action="test",
            from_instance="s",
            to_instance="terminal-1",
            correlation_id="c-1",
            metadata={"_message_id": "orig", "_channel": "dispatch"},
        )

        def handler(d: DispatchMessage):
            raise RuntimeError("boom")

        reply_id = bus.handle_dispatch(dispatch, handler)
        assert reply_id is not None
        # Should have sent a FAILED result
        call_args = bus.store.reply.call_args
        body = call_args.kwargs["body"]
        parsed = DispatchResult.from_message_body(body)
        assert parsed.status == DispatchStatus.FAILED
        assert "boom" in parsed.error

    def test_handle_dispatch_missing_metadata(self, tmp_path):
        bus = self._bus(tmp_path)
        dispatch = DispatchMessage(
            action="test",
            from_instance="s",
            to_instance="terminal-1",
            # No _message_id or _channel in metadata
        )

        def handler(d):
            return DispatchStatus.COMPLETED, {}, None

        reply_id = bus.handle_dispatch(dispatch, handler)
        assert reply_id is None


class TestGlobalBus:
    def test_set_get(self):
        mock_bus = MagicMock()
        set_global_bus(mock_bus)
        assert get_global_bus() is mock_bus
        set_global_bus(None)  # Reset

    def test_default_none(self):
        set_global_bus(None)
        assert get_global_bus() is None
